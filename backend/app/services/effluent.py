"""出水监测业务规则：状态流转、字段校验与筛选口径都收在这里。

异常口径只有一条：状态为「已超标」即异常（abnormal=True），「标记超标」与
「判定达标」两个动作都按同一口径回写，概览、列表与刷新后的结论保持一致。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "effluent"
REQUIRED_FIELDS = ["监测编号", "采样时间", "出水流量"]
STATUS_ORDER = ["待检测", "检测中", "已达标", "已超标"]
ACTION_RULES = {"开始检测": "检测中", "判定达标": "已达标", "标记超标": "已超标"}
ABNORMAL_STATUS = "已超标"
JUDGED_STATUSES = {"已达标", "已超标"}
PENDING_STATUSES = {"待检测", "检测中"}

# 判定达标时逐项核对的水质指标：量程是仪表有效读数范围，超量程视为无效数据；
# 限值取一级 A 排放标准，超过限值只能走「标记超标」，不能判达标。
INDICATOR_RULES: dict[str, dict[str, Any]] = {
    "化学需氧量": {"unit": "mg/L", "range": (0.0, 500.0), "limit": 50.0, "required": True},
    "氨氮浓度": {"unit": "mg/L", "range": (0.0, 50.0), "limit": 5.0, "required": False},
    "总磷浓度": {"unit": "mg/L", "range": (0.0, 20.0), "limit": 0.5, "required": False},
}
CONCLUSION_FIELD = "达标判定"
NOTE_FIELD = "判定说明"
STATUS_FIELD = "监测状态"


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
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry[CONCLUSION_FIELD] = "待检测，尚未判定"
        entry[NOTE_FIELD] = ""
        entry[STATUS_FIELD] = STATUS_ORDER[0]
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"出水记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于出水监测可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "判定达标":
            reason = self._compliance_reason(entry)
            if reason:
                # 判定被拦下时状态不动，只把本次失败原因覆盖到说明里，
                # 重试成功或改走标记超标后会被新结论覆盖，不会残留。
                entry[NOTE_FIELD] = reason
                return None, reason
            conclusion = self._compliance_note(entry)
        elif action == "标记超标":
            conclusion = self._exceed_note(entry, values)
        else:
            conclusion = "已开始检测，等待化学需氧量、氨氮浓度、总磷浓度结果"

        entry["status"] = target
        entry["pending"] = target in PENDING_STATUSES
        entry["abnormal"] = target == ABNORMAL_STATUS
        entry[STATUS_FIELD] = target
        entry[CONCLUSION_FIELD] = target if action != "开始检测" else "检测中，尚未判定"
        entry[NOTE_FIELD] = conclusion
        return entry, f"出水记录已{action}：{conclusion}"

    def _compliance_reason(self, entry: dict[str, Any]) -> str:
        """返回不能判定达标的原因；全部通过时返回空串。每个分支都要给出可读说明。"""
        for name, rule in INDICATOR_RULES.items():
            unit = rule["unit"]
            low, high = rule["range"]
            limit = rule["limit"]
            raw = str(entry.get(name) or "").strip()
            if not raw:
                if rule["required"]:
                    return (
                        f"{name}为空，缺少判定依据，不能判定达标；"
                        f"请先补录{name}检测数据后重试"
                    )
                continue
            try:
                value = float(raw)
            except ValueError:
                return (
                    f"{name}「{raw}」不是有效数值，不能判定达标；"
                    f"请核对录入数据后重试"
                )
            if value < low or value > high:
                return (
                    f"{name}实测 {value:g} {unit}，超出仪表量程 {low:g}~{high:g} {unit}，"
                    f"读数无效，不能判定达标；请重新采样复测后重试"
                )
            if value > limit:
                return (
                    f"{name}实测 {value:g} {unit}，超过排放限值 {limit:g} {unit}，"
                    f"不能判定达标；如确认超标请改走「标记超标」"
                )
        return ""

    def _compliance_note(self, entry: dict[str, Any]) -> str:
        cod = float(str(entry.get("化学需氧量") or 0))
        return (
            f"化学需氧量 {cod:g} mg/L（限值 50 mg/L）等指标均在量程与排放限值内，判定达标"
        )

    def _exceed_note(self, entry: dict[str, Any], values: dict[str, Any]) -> str:
        factor = str(values.get("超标项目") or "").strip()
        if factor in INDICATOR_RULES:
            rule = INDICATOR_RULES[factor]
            unit = rule["unit"]
            limit = rule["limit"]
            raw = str(entry.get(factor) or "").strip()
            if not raw:
                return (
                    f"超标项目：{factor}（排放限值 {limit:g} {unit}），"
                    f"实测数据待补录，按现场复核结果标记"
                )
            try:
                value = float(raw)
            except ValueError:
                return (
                    f"超标项目：{factor}（实测值「{raw}」待复核，"
                    f"排放限值 {limit:g} {unit}），按现场复核结果标记"
                )
            if value > limit:
                return (
                    f"超标项目：{factor}，实测 {value:g} {unit}，"
                    f"超过排放限值 {limit:g} {unit}"
                )
            return (
                f"超标项目：{factor}，实测 {value:g} {unit}（排放限值 {limit:g} {unit}，"
                f"自动判定未超限），按现场复核结果标记超标"
            )
        if factor:
            return f"超标项目：{factor}，按现场复核结果标记超标"
        return "现场复核确认超标，异常量已计入概览统计"

    def stats(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """列表页统计卡口径与概览一致：超标次数直接数 abnormal 标记，不带分页。"""
        rows, _ = self.list_entries(keyword=keyword, status=status, page=1, size=100000)
        judged = [row for row in rows if row.get("status") in JUDGED_STATUSES]
        passed = sum(1 for row in judged if row.get("status") == "已达标")
        abnormal = sum(1 for row in rows if row.get("abnormal"))

        today = date.today().isoformat()
        today_flow = 0.0
        for row in rows:
            if str(row.get("采样时间") or "").strip() != today:
                continue
            try:
                today_flow += float(row.get("出水流量"))
            except (TypeError, ValueError):
                continue

        if judged:
            rate = f"{round(passed / len(judged) * 100)}%"
        else:
            rate = "暂无已判定记录"
        return [
            {"label": "今日出水量", "value": round(today_flow, 2)},
            {"label": "达标率", "value": rate},
            {"label": "超标次数", "value": abnormal},
        ]
