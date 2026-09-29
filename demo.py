"""demo.py: 演示强杀恢复时间线 + 多进程争抢耗时分布 + 租约使用报告。

运行: python3 demo.py
"""

import multiprocessing as mp
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from filelease import FileLeaseLock
from leasereport import build_report, check_consistency, read_events, render_text


def demo_kill_recovery(workdir):
    print("=" * 60)
    print("演示 1: 持锁进程被 kill -9 后的租约恢复时间线")
    print("=" * 60)
    ttl = 2.0
    lock_path = os.path.join(workdir, "demo.lock")
    marker = os.path.join(workdir, "marker")
    child_code = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from filelease import FileLeaseLock\n"
        "lease = FileLeaseLock(%r, holder_id='victim', ttl=%r).acquire(timeout=5)\n"
        "open(%r, 'w').write('%%.6f' %% time.time())\n"
        "time.sleep(60)\n"
        % (os.path.dirname(os.path.abspath(__file__)), lock_path, ttl, marker))
    t0 = time.time()
    child = subprocess.Popen([sys.executable, "-c", child_code])
    while not os.path.exists(marker):
        time.sleep(0.02)
    t_child = float(open(marker).read())
    time.sleep(0.3)
    t_kill = time.time()
    child.send_signal(signal.SIGKILL)
    child.wait()
    lock = FileLeaseLock(lock_path, holder_id="recoverer", ttl=5.0)
    lease = lock.acquire(timeout=ttl + 5)
    t_got = time.time()
    print("  t=+%.3fs  子进程获取锁 (gen=1)" % (t_child - t0))
    print("  t=+%.3fs  子进程被 SIGKILL" % (t_kill - t0))
    print("  t=+%.3fs  租约到期 (预期)" % (t_child + ttl - t0))
    print("  t=+%.3fs  恢复方抢占成功 (gen=%d)" % (t_got - t0, lease.generation))
    print("  -> kill 到恢复耗时 %.3fs (理论下限 %.3fs)"
          % (t_got - t_kill, t_child + ttl - t_kill))
    lease.release()


def _worker(lock_path, holder, rounds, out):
    lock = FileLeaseLock(lock_path, holder_id=holder, ttl=1.0)
    rows = []
    for _ in range(rounds):
        t0 = time.monotonic()
        lease = lock.acquire(timeout=120)
        lat = time.monotonic() - t0
        time.sleep(0.02)
        lease.release()
        rows.append((lat, holder))
    out.put(rows)


def demo_contention(workdir, procs=8, rounds=5):
    print("=" * 60)
    print("演示 2: %d 进程争抢, 每进程 %d 轮, 获取耗时分布" % (procs, rounds))
    print("=" * 60)
    lock_path = os.path.join(workdir, "contend.lock")
    q = mp.Queue()
    ps = [mp.Process(target=_worker, args=(lock_path, "p%d" % i, rounds, q))
          for i in range(procs)]
    for p in ps:
        p.start()
    lats = []
    for _ in ps:
        lats.extend(row[0] for row in q.get())
    for p in ps:
        p.join()
    lats.sort()
    n = len(lats)
    pct = lambda p: lats[min(n - 1, int((n - 1) * p))]
    print("  样本数=%d  min=%.4fs  p50=%.4fs  p90=%.4fs  p99=%.4fs  max=%.4fs  mean=%.4fs"
          % (n, lats[0], pct(0.5), pct(0.9), pct(0.99), lats[-1], sum(lats) / n))


def demo_report(workdir):
    print("=" * 60)
    print("演示 3: 租约使用报告（等待时长/续期/抢占回收/代际变化）")
    print("=" * 60)
    lock_path = os.path.join(workdir, "report.lock")

    # 场景 1: A 持锁续期，B 并发等待 -> 等待时长 + 续期频率
    lock_a = FileLeaseLock(lock_path, holder_id="worker-A", ttl=0.5, report=True)
    lease_a = lock_a.acquire(timeout=2)
    stop, _, _ = lease_a.auto_renew(interval=0.2)
    lock_b = FileLeaseLock(lock_path, holder_id="worker-B", ttl=0.5, report=True)
    got_b = []

    def _b_acquire():
        got_b.append(lock_b.acquire(timeout=5))

    waiter = threading.Thread(target=_b_acquire)
    waiter.start()                                    # B 开始排队等待
    time.sleep(0.7)                                   # A 继续持锁续期
    stop.set()
    lease_a.release()
    waiter.join()
    got_b[0].release()

    # 场景 2: 持有者异常退出（不释放，租约过期）-> 抢占 + 回收
    lock_c = FileLeaseLock(lock_path, holder_id="crashed-C", ttl=0.4, report=True)
    lock_c.acquire(timeout=2)                         # 故意不续期不释放，模拟崩溃
    time.sleep(0.6)
    lock_d = FileLeaseLock(lock_path, holder_id="recover-D", ttl=0.5, report=True)
    lease_d = lock_d.acquire(timeout=5)
    lease_d.release()

    events = read_events(lock_path + ".events")
    print(render_text(build_report(events), lock_path=lock_path))
    ok, detail = check_consistency(lock_path)
    print("\n[一致性校验] %s: %s" % ("通过" if ok else "失败", detail))
    print("导出: python3 leasereport.py %s --start <ts> --end <ts> [--json] [--check]"
          % lock_path)


if __name__ == "__main__":
    workdir = tempfile.mkdtemp(prefix="filelease-demo-")
    try:
        demo_kill_recovery(workdir)
        demo_contention(workdir)
        demo_report(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
