"""出水监测业务规则：状态流转、字段校验与筛选口径都收在这里。

异常口径（与判定结论保持同一来源，概览/列表都读这里）：
- 状态进入「已超标」才算异常，「已达标」不算异常；
- 「已达标 / 已超标」都是终态，不再计入待处理，其余状态为待处理。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "effluent"
REQUIRED_FIELDS = ["监测编号", "采样时间", "出水流量"]
STATUS_ORDER = ["待检测", "检测中", "已达标", "已超标"]
ACTION_RULES = {"开始检测": "检测中", "判定达标": "已达标", "标记超标": "已超标"}
TERMINAL_STATUSES = {"已达标", "已超标"}
ABNORMAL_STATUSES = {"已超标"}
JUDGE_ACTIONS = {"判定达标", "标记超标"}

# 在线 COD 仪有效量程（mg/L），空值或超出量程都不能作为判定依据
COD_FIELD = "化学需氧量"
COD_MIN = 0.0
COD_MAX = 500.0

CONCLUSION = {"开始检测": "检测中，待判定", "判定达标": "达标", "标记超标": "超标"}


def _parse_number(value: Any) -> float | None:
    """把字段值解析成数值；空值或非数值一律返回 None，交由调用方说明原因。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class EffluentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("监测编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 化学需氧量等选填监测项有值就一并登记
        for field in (COD_FIELD, "氨氮浓度", "总磷浓度"):
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["达标判定"] = "待检测"
        entry["监测状态"] = STATUS_ORDER[0]
        rows.append(entry)
        return entry, []

    def statistics(self) -> dict[str, int | float]:
        """列表页统计卡口径，与概览同源：异常量按「已超标」状态统计。"""
        rows = store.rows(MODULE)
        today = date.today().isoformat()
        today_flow = 0.0
        passed = 0
        exceeded = 0
        for row in rows:
            status = row.get("status")
            if status == "已达标":
                passed += 1
            elif status == "已超标":
                exceeded += 1
            if str(row.get("采样时间") or "")[:10] == today:
                value = _parse_number(row.get("出水流量"))
                if value is not None:
                    today_flow += value
        judged = passed + exceeded
        rate = round(passed / judged * 100, 1) if judged else 0.0
        return {"today_flow": today_flow, "pass_rate": rate, "exceeded": exceeded}

    def _check_cod(self, entry: dict[str, Any]) -> str | None:
        """判定前校验化学需氧量：空数据、非数值、超出量程都要给出可读原因。"""
        raw = entry.get(COD_FIELD)
        value = _parse_number(raw)
        if value is None:
            return "化学需氧量为空或不是有效数值，无法作为判定依据，请补测后重试"
        if value < COD_MIN or value > COD_MAX:
            return (
                f"化学需氧量 {value:g} mg/L 超出仪表量程"
                f"（{COD_MIN:g}~{COD_MAX:g} mg/L），数据无效，请复测后重试"
            )
        return None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于出水监测可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        # 判定达标/超标必须有有效的化学需氧量；校验失败不改变状态，
        # 但要在结论列留下未判定原因，避免上一次的结论残留在记录里。
        if action in JUDGE_ACTIONS:
            reason = self._check_cod(entry)
            if reason is not None:
                entry["达标判定"] = f"未判定：{reason}"
                return None, reason

        entry["status"] = target
        entry["pending"] = target not in TERMINAL_STATUSES
        entry["abnormal"] = target in ABNORMAL_STATUSES
        # 每次动作都整体重写结论字段，重复标记时以最新结论为准，不留旧结论
        entry["达标判定"] = CONCLUSION[action]
        entry["监测状态"] = target
        return entry, f"出水记录已{action}"
