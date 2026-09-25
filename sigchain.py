"""sigchain — 签名凭据链的构建与逐级验证库（仅依赖 Python 标准库）。

模型
----
一条凭据链是若干级（Link）组成的有序序列：

    Link(issuer, subject, purposes, not_before, not_after, signature)

- 第 0 级为根级：必须由信任锚（trust anchor）自签，即 issuer == subject
  且 issuer 在 trust_anchors 中。
- 第 i 级（i > 0）的 issuer 必须等于第 i-1 级的 subject（链式接续）。
- 签名 = HMAC-SHA256(issuer 的密钥, 规范化的级别内容)。
  标准库没有非对称签名算法，这里用 HMAC + 密钥注册表模拟
  “签发方私钥签名 / 验证方用公钥验签”的语义：keys[issuer] 即
  该签发方的验证密钥。换成 Ed25519 等真实签名时只需替换
  _sign / _verify 两个函数，验证流程不变。

验证语义
--------
verify() 对链上每一级独立给出结论（LevelReport），整体结论为所有级别
结论的合取，外加末端用途充分性检查。每一级依次检查：

1. 深度：级序号 >= max_depth 记 DEPTH_EXCEEDED；
2. 接续：根级须为信任锚自签；非根级 issuer 须等于上级 subject，
   否则 CHAIN_BROKEN；同一 issuer 在链中重复出现（含重复级别/成环）
   也记 CHAIN_BROKEN；
3. 签名：用 issuer 的验证密钥对规范化内容验签，失败记
   SIGNATURE_MISMATCH（内容被篡改即落入此类）；
4. 时间：now < not_before 记 NOT_YET_VALID；now > not_after 记 EXPIRED；
5. 用途收紧：本级 purposes 必须是上级 purposes 的子集，
   否则 PURPOSE_ESCALATION，并报出越权级别与具体用途。

最后检查末端级别的 purposes 是否覆盖调用方要求的 required_purposes，
不足则在末端级记 PURPOSE_ESCALATION。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Set


class FailCode(Enum):
    """验证失败类型。"""

    OK = "ok"
    CHAIN_BROKEN = "chain_broken"            # 链断裂（接续错误/重复级别/非法根）
    SIGNATURE_MISMATCH = "signature_mismatch"  # 签名不匹配（含内容被篡改）
    EXPIRED = "expired"                      # 已过期
    NOT_YET_VALID = "not_yet_valid"          # 尚未生效
    PURPOSE_ESCALATION = "purpose_escalation"  # 用途越界
    DEPTH_EXCEEDED = "depth_exceeded"        # 深度超限


@dataclass(frozen=True)
class Link:
    """凭据链中的一级。"""

    issuer: str
    subject: str
    purposes: FrozenSet[str]
    not_before: int
    not_after: int
    signature: bytes

    def unsigned_dict(self) -> dict:
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "purposes": sorted(self.purposes),
            "not_before": self.not_before,
            "not_after": self.not_after,
        }


@dataclass(frozen=True)
class LevelReport:
    """链上某一级的验证结论与依据。"""

    index: int
    issuer: str
    subject: str
    ok: bool
    code: FailCode
    detail: str


@dataclass(frozen=True)
class ChainReport:
    """整条链的验证报告。"""

    ok: bool
    levels: List[LevelReport] = field(default_factory=list)
    summary: str = ""

    def render(self) -> str:
        lines = [f"整体结论: {'通过' if self.ok else '拒绝'} — {self.summary}"]
        for lv in self.levels:
            mark = "OK " if lv.ok else "FAIL"
            lines.append(
                f"  [{mark}] 第{lv.index}级 {lv.issuer} -> {lv.subject}: "
                f"{lv.code.value} ({lv.detail})"
            )
        return "\n".join(lines)


def _canonical_payload(
    issuer: str,
    subject: str,
    purposes: Iterable[str],
    not_before: int,
    not_after: int,
) -> bytes:
    """级别内容的规范化字节表示（签名/验签共用，保证可复现）。"""
    obj = {
        "issuer": issuer,
        "subject": subject,
        "purposes": sorted(purposes),
        "not_before": not_before,
        "not_after": not_after,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def _verify_signature(key: bytes, payload: bytes, signature: bytes) -> bool:
    expected = _sign(key, payload)
    return hmac.compare_digest(expected, signature)


def issue(
    keys: Mapping[str, bytes],
    issuer: str,
    subject: str,
    purposes: Iterable[str],
    not_before: int,
    not_after: int,
) -> Link:
    """用 issuer 的密钥签出一级凭据。"""
    if issuer not in keys:
        raise KeyError(f"没有签发方 {issuer!r} 的密钥")
    purpose_set = frozenset(purposes)
    payload = _canonical_payload(issuer, subject, purpose_set, not_before, not_after)
    return Link(
        issuer=issuer,
        subject=subject,
        purposes=purpose_set,
        not_before=not_before,
        not_after=not_after,
        signature=_sign(keys[issuer], payload),
    )


def verify(
    chain: List[Link],
    keys: Mapping[str, bytes],
    trust_anchors: Set[str],
    now: int,
    required_purposes: Iterable[str] = (),
    max_depth: int = 8,
) -> ChainReport:
    """逐级验证凭据链，返回每一级的结论与依据。

    参数:
        chain: 有序级别列表，第 0 级为根级（信任锚自签）。
        keys: issuer -> 验证密钥。
        trust_anchors: 允许作为根级的信任锚标识集合。
        now: 验证时刻（秒级时间戳）。
        required_purposes: 本次验证要求末端凭据具备的用途。
        max_depth: 允许的最大级数。
    """
    reports: List[LevelReport] = []
    if not chain:
        return ChainReport(ok=False, levels=[], summary="空链：没有任何级别")

    seen_edges: Set[tuple] = set()
    for index, link in enumerate(chain):
        problems: List[str] = []
        codes: List[FailCode] = []

        # 1. 深度
        if index >= max_depth:
            codes.append(FailCode.DEPTH_EXCEEDED)
            problems.append(f"级序号 {index} 超出允许的最大深度 {max_depth}")

        # 2. 接续与根级合法性
        if index == 0:
            if link.issuer != link.subject:
                codes.append(FailCode.CHAIN_BROKEN)
                problems.append(
                    f"根级必须由信任锚自签，但 issuer={link.issuer!r} "
                    f"!= subject={link.subject!r}"
                )
            elif link.issuer not in trust_anchors:
                codes.append(FailCode.CHAIN_BROKEN)
                problems.append(f"根级签发方 {link.issuer!r} 不是信任锚")
        else:
            prev = chain[index - 1]
            if link.issuer != prev.subject:
                codes.append(FailCode.CHAIN_BROKEN)
                problems.append(
                    f"链断裂：上级主体是 {prev.subject!r}，"
                    f"本级签发方却是 {link.issuer!r}"
                )
        edge = (link.issuer, link.subject)
        if edge in seen_edges:
            codes.append(FailCode.CHAIN_BROKEN)
            problems.append(
                f"签发边 {link.issuer!r} -> {link.subject!r} 在链中重复出现"
                "（重复级别/成环）"
            )
        seen_edges.add(edge)

        # 3. 签名
        key = keys.get(link.issuer)
        payload = _canonical_payload(
            link.issuer, link.subject, link.purposes,
            link.not_before, link.not_after,
        )
        if key is None:
            codes.append(FailCode.SIGNATURE_MISMATCH)
            problems.append(f"未知签发方 {link.issuer!r}，没有验证密钥")
        elif not _verify_signature(key, payload, link.signature):
            codes.append(FailCode.SIGNATURE_MISMATCH)
            problems.append("签名与级别内容不匹配（内容可能被篡改）")

        # 4. 有效期
        if now < link.not_before:
            codes.append(FailCode.NOT_YET_VALID)
            problems.append(
                f"尚未生效：not_before={link.not_before} > now={now}"
            )
        if now > link.not_after:
            codes.append(FailCode.EXPIRED)
            problems.append(f"已过期：not_after={link.not_after} < now={now}")

        # 5. 用途逐级收紧
        if index > 0:
            extra = sorted(link.purposes - chain[index - 1].purposes)
            if extra:
                codes.append(FailCode.PURPOSE_ESCALATION)
                problems.append(
                    f"第{index}级越权：申请了上级未授予的用途 {extra}"
                )

        ok = not codes
        code = FailCode.OK if ok else codes[0]
        detail = "校验通过" if ok else "；".join(problems)
        reports.append(
            LevelReport(
                index=index,
                issuer=link.issuer,
                subject=link.subject,
                ok=ok,
                code=code,
                detail=detail,
            )
        )

    # 6. 末端用途充分性
    required = frozenset(required_purposes)
    if required:
        leaf = chain[-1]
        missing = sorted(required - leaf.purposes)
        if missing:
            last = reports[-1]
            detail = (
                f"末端凭据缺少本次验证要求的用途 {missing}"
                if last.ok
                else f"{last.detail}；末端凭据缺少本次验证要求的用途 {missing}"
            )
            reports[-1] = LevelReport(
                index=last.index,
                issuer=last.issuer,
                subject=last.subject,
                ok=False,
                code=FailCode.PURPOSE_ESCALATION,
                detail=detail,
            )

    ok = all(lv.ok for lv in reports)
    if ok:
        summary = f"共 {len(reports)} 级，逐级校验全部通过"
    else:
        failed = [lv.index for lv in reports if not lv.ok]
        summary = f"共 {len(reports)} 级，第 {failed} 级校验失败"
    return ChainReport(ok=ok, levels=reports, summary=summary)
