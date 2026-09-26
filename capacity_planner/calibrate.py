"""模拟校准：同一参数下对比解析模型预测与模拟结果，偏差超阈值即告警。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import mgc_metrics, p_wait_exceeds, ModelError
from .simulate import SimConfig, run_simulation

DEFAULT_THRESHOLD = 0.15  # 相对偏差告警阈值 15%


@dataclass
class MetricComparison:
    name: str
    model: float
    simulated: float
    rel_error: float | None   # 两者都为 0 时为 None
    alert: bool


@dataclass
class CalibrationReport:
    comparisons: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.alerts

    def format(self) -> str:
        lines = ["指标                模型预测        模拟值        相对偏差   状态"]
        for cmp in self.comparisons:
            rel = "n/a" if cmp.rel_error is None else f"{cmp.rel_error:8.1%}"
            lines.append(f"{cmp.name:<18}{cmp.model:>12.4g}{cmp.simulated:>14.4g}"
                         f"{rel:>12}   {'ALERT' if cmp.alert else 'ok'}")
        for a in self.alerts:
            lines.append(f"[告警] {a}")
        for n in self.notes:
            lines.append(f"[说明] {n}")
        return "\n".join(lines)


def _compare(name, model_val, sim_val, threshold):
    if model_val == 0.0 and sim_val == 0.0:
        return MetricComparison(name, model_val, sim_val, None, False)
    denom = max(abs(model_val), abs(sim_val), 1e-12)
    rel = abs(model_val - sim_val) / denom
    return MetricComparison(name, model_val, sim_val, rel, rel > threshold)


def calibrate(cfg: SimConfig, threshold: float = DEFAULT_THRESHOLD) -> CalibrationReport:
    """对 cfg 指定的系统同时跑解析模型与模拟，输出对比报告。"""
    if not 0 < threshold < 1:
        raise ModelError("threshold 必须在 (0,1)")
    model = mgc_metrics(cfg.lam, cfg.mean_service, cfg.scv_service
                        if cfg.dist != "exponential" else 1.0, cfg.c)
    sim = run_simulation(cfg)
    rep = CalibrationReport()
    if model.unstable:
        rep.notes.append("rho>=1：模型无稳态解，模拟队列持续增长，无法校准；"
                         "这本身就是模型失效边界。")
        rep.comparisons.append(MetricComparison(
            "mean_queue", float("inf"), sim.mean_queue, None, True))
        rep.alerts.append("系统不稳定：模型与模拟不可比，需先扩容或限流")
        return rep

    rep.comparisons.append(_compare("mean_wq", model.wq, sim.mean_wait, threshold))
    rep.comparisons.append(_compare("mean_lq", model.lq, sim.mean_queue, threshold))
    if cfg.timeout is not None:
        mu = 1.0 / cfg.mean_service
        model_to = p_wait_exceeds(model, cfg.c, mu, cfg.timeout)
        floor = 5.0 / max(sim.arrived, 1)  # 模拟可分辨的概率下限
        if model_to < floor and sim.p_timeout == 0.0:
            rep.comparisons.append(
                MetricComparison("p_timeout", model_to, sim.p_timeout,
                                 None, False))
            rep.notes.append(
                f"p_timeout 低于模拟可分辨下限({floor:.1e})，视为一致")
        else:
            rep.comparisons.append(
                _compare("p_timeout", model_to, sim.p_timeout, threshold))
    for cmp in rep.comparisons:
        if cmp.alert:
            rep.alerts.append(
                f"{cmp.name}: 模型 {cmp.model:.4g} vs 模拟 {cmp.simulated:.4g}"
                f"，相对偏差 {cmp.rel_error:.1%} 超过阈值 {threshold:.0%}")
    if model.rho > 0.85:
        rep.notes.append("rho>0.85：处于高利用区，等待对到达率误差极敏感，"
                         "即使校准通过也应保留余量")
    if cfg.dist == "lognormal" and cfg.scv_service > 2.0:
        rep.notes.append("长尾服务(SCV>2)：Allen-Cunneen 近似系统性偏乐观，"
                         "建议以模拟值为准")
    rep.notes.extend(sim.notes)
    return rep
