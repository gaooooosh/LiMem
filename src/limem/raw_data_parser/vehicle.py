"""Strict adapter for LiMem Eval vehicle Session JSON."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Annotated, Any, Callable, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from .base import JsonObject, RawDataParseError, RawDataParser
from .models import RawEvent, SessionInput
from .vehicle_protocol import (
    ACTOR_LABELS as PROTOCOL_ACTOR_LABELS,
    EVENT_LABELS as PROTOCOL_EVENT_LABELS,
    TRIGGER_SOURCES,
    flatten_and_validate,
    render_leaf,
    render_payload_value,
    validate_trigger_payload,
)

_MISSING = object()

_NON_EMPTY_SOURCE_TEXT = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]

_EVENT_LABELS = dict(PROTOCOL_EVENT_LABELS)

_SESSION_BOUNDARY_EVENT_TYPES = frozenset(
    {"SessionStartEvent", "SessionEndEvent"}
)

_RECORD_TYPE_LABELS = {
    "heartbeat": "定时触发",
    "trigger": "状态发生变化",
}
_ACTOR_LABELS = dict(PROTOCOL_ACTOR_LABELS)
_PLAY_STATUS = {
    "playing": "正在播放",
    "pause": "已暂停",
    "stop": "已停止",
    "buffering": "缓冲中",
}
_CONTENT_MODE = {
    "idle": "待机页",
    "home": "主页",
    "navigation": "导航页",
    "media": "媒体页",
    "climate": "空调页",
    "charging": "充电页",
    "settings": "设置页",
    "camera": "影像页",
    "seat": "座椅页",
    "game": "游戏页",
    "kids": "儿童页",
    "video": "视频页",
    "driving": "驾驶页",
}
_AC_MODE = {
    "auto": "自动",
    "manual": "手动",
    "eco": "节能",
    "defrost": "除霜",
    "rest": "休息",
}
_AIRFLOW_MODE = {
    "face": "面部",
    "foot": "脚部",
    "defrost": "除霜",
    "face_foot": "面部和脚部",
    "auto": "自动",
}
_GEAR = {"D": "D挡", "P": "P挡"}
_GEAR.update({"R": "R挡", "N": "N挡"})
_SOUND_ZONE = {
    "all": "全车",
    "driver": "主驾",
    "front": "前排",
    "rear": "后排",
    "row2": "二排",
    "row3": "三排",
}
_SEAT_NAMES = {
    "seat.row1.left.occupied": "主驾",
    "seat.row1.right.occupied": "副驾",
    "seat.row2.left.occupied": "左后排",
}
_WINDOW_NAMES = {
    "body.window.row1.left.open_pct": "主驾车窗",
    "body.window.row1.right.open_pct": "副驾车窗",
    "body.window.row2.left.open_pct": "左后排车窗",
    "body.window.row2.right.open_pct": "右后排车窗",
}
_VENTILATION_NAMES = {
    "seat.row1.left.ventilation_level": "主驾座椅",
    "seat.row1.right.ventilation_level": "副驾座椅",
}

class _TriggerSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    eventType: str
    startTime: str
    endTime: str | None
    source: str
    actor: str
    payload: dict[str, JsonValue]
    detail: str
    relatedAction: str


class _RecordSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    record_id: str
    timestamp: str
    type: Literal["heartbeat", "trigger"]
    trigger: _TriggerSource | None
    device_tree: dict[str, JsonValue]

    @model_validator(mode="after")
    def check_trigger(self) -> _RecordSource:
        if self.type == "trigger" and self.trigger is None:
            raise ValueError("trigger record requires trigger data")
        if self.type == "heartbeat" and self.trigger is not None:
            raise ValueError("heartbeat record cannot contain trigger data")
        return self


class _SessionSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: Annotated[
        str,
        StringConstraints(min_length=1, pattern=r"^[^\r\n]*\S[^\r\n]*$"),
    ]
    vehicle_id: _NON_EMPTY_SOURCE_TEXT
    user_id: _NON_EMPTY_SOURCE_TEXT
    records: list[_RecordSource] = Field(min_length=1)

    @field_validator("records")
    @classmethod
    def require_unique_record_ids(
        cls,
        records: list[_RecordSource],
    ) -> list[_RecordSource]:
        record_ids = [record.record_id for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("record_id must be unique within a Session")
        return records


def _number_text(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _translate(value: str, labels: dict[str, str], field: str) -> str:
    try:
        return labels[value]
    except KeyError as error:
        raise ValueError(f"unsupported {field}: {value!r}") from error


def _parse_timestamp(value: str) -> datetime:
    occurred_at = datetime.fromisoformat(value)
    if occurred_at.tzinfo is None:
        raise ValueError("record timestamp must include a timezone")
    return occurred_at


def _path(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            raise ValueError(f"{dotted_path!r} crosses a non-object value")
        if part not in current:
            return _MISSING
        current = current[part]
    return current


def _optional_string(data: dict[str, Any], dotted_path: str) -> str | None:
    value = _path(data, dotted_path)
    if value is _MISSING or value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{dotted_path!r} must be a string")
    return value


def _optional_bool(data: dict[str, Any], dotted_path: str) -> bool | None:
    value = _path(data, dotted_path)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{dotted_path!r} must be a boolean")
    return value


def _optional_number(data: dict[str, Any], dotted_path: str) -> int | float | None:
    value = _path(data, dotted_path)
    if value is _MISSING or value is None:
        return None
    return cast(int | float, value)


def _format_number(unit: str) -> Callable[[JsonValue], str]:
    def format_value(value: JsonValue) -> str:
        return f"{_number_text(cast(int | float, value))}{unit}"

    return format_value


def _format_string(value: JsonValue) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected non-empty string, got {value!r}")
    return value


def _format_window(value: JsonValue) -> str:
    value = cast(int | float, value)
    return "关闭" if value == 0 else f"开启{_number_text(value)}%"


def _format_ventilation(value: JsonValue) -> str:
    value = cast(int | float, value)
    return "关闭" if value == 0 else f"{_number_text(value)}档"


def _format_bool(on: str, off: str) -> Callable[[JsonValue], str]:
    def format_value(value: JsonValue) -> str:
        if not isinstance(value, bool):
            raise ValueError(f"expected boolean, got {value!r}")
        return on if value else off

    return format_value


def _format_enum(labels: dict[str, str], field: str) -> Callable[[JsonValue], str]:
    def format_value(value: JsonValue) -> str:
        if not isinstance(value, str):
            raise ValueError(f"expected {field} string, got {value!r}")
        return _translate(value, labels, field)

    return format_value


_PAYLOAD_FIELDS: dict[str, tuple[str, Callable[[JsonValue], str]]] = {
    "cabin.cabin_env.noise_db": ("cabin_noise", _format_number("dB")),
    "cabin.media.category": ("media_category", _format_string),
    "cabin.media.play_status": (
        "media_status",
        _format_enum(_PLAY_STATUS, "media status"),
    ),
    "cabin.media.title": ("media_title", _format_string),
    "cabin.media.volume": ("media_volume", _format_number("")),
    "cabin.screen.center.brightness_level": (
        "screen_brightness",
        _format_number("级"),
    ),
    "cabin.screen.center.content_mode": (
        "screen_content",
        _format_enum(_CONTENT_MODE, "screen content mode"),
    ),
    "hvac.ac_unit.ac_mode": ("climate_mode", _format_enum(_AC_MODE, "AC mode")),
    "hvac.airflow.airflow_mode": (
        "airflow_direction",
        _format_enum(_AIRFLOW_MODE, "airflow mode"),
    ),
    "hvac.airflow.fan_speed": ("fan_speed", _format_number("档")),
    "hvac.zone.all.target_temp_c": ("target_temperature", _format_number("℃")),
    "navigation.active": (
        "navigation_status",
        _format_bool("已开启", "未开启"),
    ),
    "navigation.destination": ("navigation_destination", _format_string),
    "navigation.distance_km": ("navigation_distance", _format_number("km")),
    "navigation.eta_minutes": ("navigation_eta", _format_number("分钟")),
    "power.charging.charge_status": (
        "charging_status",
        _format_enum({"charging": "充电中", "idle": "未充电"}, "charging status"),
    ),
    "power.charging.power_kw": ("charging_power", _format_number("kW")),
    "power.charging.remain_min": ("charging_remaining", _format_number("分钟")),
    "power.traction_battery.alert_threshold_pct": (
        "low_battery_threshold",
        _format_number("%"),
    ),
    "power.traction_battery.soc_pct": ("battery_level", _format_number("%")),
    "schedule.in_meeting": (
        "meeting_status",
        _format_bool("会议中", "未在会议中"),
    ),
    "schedule.minutes_to_start": ("schedule_starts_in", _format_number("分钟")),
    "schedule.next_event": ("next_schedule", _format_string),
    "seat.row1.left.ventilation_level": (
        "driver_seat_ventilation",
        _format_ventilation,
    ),
}


class VehicleRawDataParser(RawDataParser):
    """Parse one VehicleLogPackage Session."""

    @property
    def source(self) -> str:
        return "vehicle_logs"

    @property
    def format_description(self) -> str:
        return (
            "适配 LiMem Eval VehicleLogPackage 中的单个 Session JSON。"
            "输入包含 session_id、vehicle_id、user_id 和 records；"
            "Record 类型为 trigger 或 heartbeat。"
        )

    def parse(self, session_json: JsonObject) -> SessionInput:
        source_session = _SessionSource.model_validate(session_json)
        raw_records = cast(list[dict[str, JsonValue]], session_json["records"])

        raw_events = []
        for position, record in enumerate(source_session.records):
            try:
                if (
                    record.trigger is not None
                    and record.trigger.eventType in _SESSION_BOUNDARY_EVENT_TYPES
                ):
                    self._validate_session_boundary(record)
                    continue
                raw_event = self._parse_record(
                    source_session,
                    record,
                    position,
                    deepcopy(raw_records[position]),
                )
            except ValueError as exc:
                raise RawDataParseError(("records", position), str(exc)) from exc
            raw_events.append(raw_event)
        return SessionInput(
            session_ref=source_session.session_id,
            record_count=len(source_session.records),
            raw_events=raw_events,
        )

    @staticmethod
    def _validate_session_boundary(record: _RecordSource) -> None:
        _parse_timestamp(record.timestamp)
        if record.device_tree:
            raise ValueError("session boundary device_tree must be empty")
        if record.trigger is None:
            raise ValueError("session boundary requires trigger data")
        if record.trigger.payload:
            raise ValueError("session boundary payload must be empty")

    def _parse_record(
        self,
        session: _SessionSource,
        record: _RecordSource,
        position: int,
        raw_payload: dict[str, JsonValue],
    ) -> RawEvent:
        occurred_at = _parse_timestamp(record.timestamp)

        device_tree = record.device_tree
        device_leaves = self._validate_device_tree(device_tree)
        trigger = record.trigger
        event = _RECORD_TYPE_LABELS["heartbeat"]
        actor = None
        action = None
        detail = None
        payload = None
        if trigger is not None:
            self._validate_trigger_metadata(trigger)
            event = _translate(trigger.eventType, _EVENT_LABELS, "event type")
            actor = _translate(trigger.actor, _ACTOR_LABELS, "actor")
            action = trigger.relatedAction or None
            detail = trigger.detail or None
            payload_leaves = validate_trigger_payload(
                trigger.payload,
                event_type=trigger.eventType,
                device_tree=device_tree,
            )
            payload = self._render_payload(payload_leaves) or None

        return RawEvent(
            raw_event_id=record.record_id,
            session_ref=session.session_id,
            session_position=position,
            user_ref=session.user_id,
            vehicle_ref=session.vehicle_id,
            source=self.source,
            occurred_at=occurred_at,
            record_type=_translate(record.type, _RECORD_TYPE_LABELS, "record type"),
            event=event,
            actor=actor,
            action=action,
            detail=detail,
            payload=payload,
            location=self._with_protocol_fields(
                self._render_location(device_tree),
                device_leaves,
                ("sensing.location",),
                {"sensing.location.poi_name"},
            ),
            environment=self._with_protocol_fields(
                self._render_environment(device_tree),
                device_leaves,
                ("sensing.env", "cabin.cabin_env"),
                {
                    "sensing.env.outside_temp.temp_c",
                    "sensing.env.light.illuminance_lux",
                    "cabin.cabin_env.cabin_temp_c",
                    "cabin.cabin_env.noise_db",
                },
            ),
            vehicle=self._with_protocol_fields(
                self._render_vehicle(device_tree),
                device_leaves,
                ("chassis",),
                {"chassis.powertrain.gear", "chassis.powertrain.speed_kmh"},
            ),
            occupants=self._with_protocol_fields(
                self._render_occupants(device_tree),
                device_leaves,
                ("cabin.occupancy",),
            ),
            media=self._with_protocol_fields(
                self._render_media(device_tree),
                device_leaves,
                ("cabin.media", "cabin.audio"),
                {
                    "cabin.audio.sound_zone",
                    "cabin.media.app_name",
                    "cabin.media.artist",
                    "cabin.media.category",
                    "cabin.media.media_source_type",
                    "cabin.media.play_status",
                    "cabin.media.title",
                    "cabin.media.volume",
                },
            ),
            screen=self._with_protocol_fields(
                self._render_screen(device_tree),
                device_leaves,
                ("cabin.screen",),
                {
                    "cabin.screen.center.brightness_level",
                    "cabin.screen.center.content_mode",
                    "cabin.screen.hmi.app",
                    "cabin.screen.hmi.description",
                    "cabin.screen.hmi.last_control_name",
                    "cabin.screen.hmi.last_input_type",
                    "cabin.screen.hmi.media_name",
                    "cabin.screen.hmi.play_status",
                },
            ),
            navigation=self._with_protocol_fields(
                self._render_navigation(device_tree),
                device_leaves,
                ("navigation",),
                {
                    "navigation.active",
                    "navigation.start_poi",
                    "navigation.destination",
                    "navigation.distance_km",
                    "navigation.eta_minutes",
                    "navigation.route_summary",
                },
            ),
            energy=self._with_protocol_fields(
                self._render_energy(device_tree),
                device_leaves,
                ("power",),
                {
                    "power.charge_port.plug_connected",
                    "power.charge_port.port_status",
                    "power.charging.charge_status",
                    "power.charging.power_kw",
                    "power.charging.remain_min",
                    "power.traction_battery.alert_threshold_pct",
                    "power.traction_battery.range_km",
                    "power.traction_battery.soc_pct",
                },
            ),
            schedule=self._render_schedule(device_tree),
            climate=self._with_protocol_fields(
                self._render_climate(device_tree),
                device_leaves,
                ("hvac",),
                {
                    "hvac.ac_unit.ac_mode",
                    "hvac.ac_unit.ac_on",
                    "hvac.airflow.airflow_mode",
                    "hvac.airflow.fan_speed",
                    "hvac.zone.all.target_temp_c",
                    "hvac.zone.front_left.target_temp_c",
                    "hvac.zone.front_right.target_temp_c",
                },
            ),
            windows=self._render_windows(device_tree),
            seats=self._with_protocol_fields(
                self._render_seats(device_tree),
                device_leaves,
                ("seat",),
                {
                    "seat.row1.left.ventilation_level",
                    "seat.row1.right.ventilation_level",
                    "seat.passenger_count",
                    *{
                        path
                        for path in device_leaves
                        if path.endswith(".occupied")
                    },
                },
            ),
            dialogue=self._with_protocol_fields(
                self._render_dialogue(device_tree),
                device_leaves,
                ("cabin.dialogue",),
                {
                    "cabin.dialogue.do_not_disturb",
                    "cabin.dialogue.speaker_seat",
                },
            ),
            body=self._render_protocol_fields(
                device_leaves,
                ("body",),
                excluded={
                    *{
                        path
                        for path in device_leaves
                        if path.startswith("body.window.")
                        and path.endswith(".open_pct")
                    }
                },
            ),
            lighting=self._render_protocol_fields(device_leaves, ("lighting",)),
            wiper=self._render_protocol_fields(device_leaves, ("wiper",)),
            perception=self._render_protocol_fields(
                device_leaves,
                ("sensing.dms_camera", "sensing.cabin_camera"),
            ),
            safety=self._render_protocol_fields(device_leaves, ("safety",)),
            connectivity=self._render_protocol_fields(
                device_leaves,
                ("connectivity",),
            ),
            maintenance=self._render_protocol_fields(
                device_leaves,
                ("maintenance",),
            ),
            raw_payload=raw_payload,
        )

    @staticmethod
    def _validate_device_tree(
        device_tree: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return flatten_and_validate(
            device_tree,
            location="device tree",
        )

    @staticmethod
    def _validate_trigger_metadata(trigger: _TriggerSource) -> None:
        started_at = _parse_timestamp(trigger.startTime)
        if trigger.endTime is not None:
            ended_at = _parse_timestamp(trigger.endTime)
            if ended_at < started_at:
                raise ValueError("trigger endTime must not be before startTime")
        if trigger.source not in TRIGGER_SOURCES:
            raise ValueError(f"unsupported trigger source: {trigger.source!r}")
        if trigger.actor not in _ACTOR_LABELS:
            raise ValueError(f"unsupported actor: {trigger.actor!r}")
        if trigger.eventType == "AgentEvent":
            raise ValueError("AgentEvent cannot trigger an input Record")
        if trigger.eventType not in _EVENT_LABELS:
            raise ValueError(f"unsupported event type: {trigger.eventType!r}")

    def _render_payload(self, leaves: dict[str, JsonValue]) -> dict[str, str]:
        rendered: dict[str, str] = {}
        for source_path, value in leaves.items():
            if source_path in _PAYLOAD_FIELDS:
                output_name, formatter = _PAYLOAD_FIELDS[source_path]
                if value is None or value == "" or value == []:
                    rendered[output_name] = render_payload_value(source_path, value)
                else:
                    rendered[output_name] = formatter(value)
            else:
                rendered[source_path] = render_payload_value(source_path, value)
        return rendered

    @staticmethod
    def _render_protocol_fields(
        leaves: dict[str, JsonValue],
        prefixes: tuple[str, ...],
        excluded: set[str] | None = None,
    ) -> str | None:
        excluded = excluded or set()
        parts = [
            rendered
            for path, value in leaves.items()
            if path not in excluded
            and any(path == prefix or path.startswith(f"{prefix}.") for prefix in prefixes)
            and (rendered := render_leaf(path, value)) is not None
        ]
        return "；".join(parts) or None

    @classmethod
    def _with_protocol_fields(
        cls,
        current: str | None,
        leaves: dict[str, JsonValue],
        prefixes: tuple[str, ...],
        excluded: set[str] | None = None,
    ) -> str | None:
        additional = cls._render_protocol_fields(leaves, prefixes, excluded)
        return "；".join(part for part in (current, additional) if part) or None

    @staticmethod
    def _render_location(tree: dict[str, Any]) -> str | None:
        poi_name = _optional_string(tree, "sensing.location.poi_name")
        return f"位置：{poi_name}" if poi_name else None

    @staticmethod
    def _render_environment(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        outside = _optional_number(tree, "sensing.env.outside_temp.temp_c")
        cabin = _optional_number(tree, "cabin.cabin_env.cabin_temp_c")
        noise = _optional_number(tree, "cabin.cabin_env.noise_db")
        light = _optional_number(tree, "sensing.env.light.illuminance_lux")
        if outside is not None:
            parts.append(f"车外{_number_text(outside)}℃")
        if cabin is not None:
            parts.append(f"舱内{_number_text(cabin)}℃")
        if noise is not None:
            parts.append(f"噪声{_number_text(noise)}dB")
        if light is not None:
            parts.append(f"照度{_number_text(light)}lux")
        return "；".join(parts) or None

    @staticmethod
    def _render_vehicle(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        gear = _optional_string(tree, "chassis.powertrain.gear")
        speed = _optional_number(tree, "chassis.powertrain.speed_kmh")
        if gear is not None:
            parts.append(_translate(gear, _GEAR, "gear"))
        if speed is not None:
            parts.append(f"车速{_number_text(speed)}km/h")
        return "；".join(parts) or None

    @staticmethod
    def _render_occupants(tree: dict[str, Any]) -> str | None:
        count = _optional_number(tree, "seat.passenger_count")
        known = [
            name
            for path, name in _SEAT_NAMES.items()
            if _optional_bool(tree, path) is True
        ]
        if count is None:
            return f"已知成员：{'、'.join(known)}" if known else None
        count_text = _number_text(count)
        if not known:
            return f"成员{count_text}人"
        if count == len(known):
            return f"成员{count_text}人：{'、'.join(known)}"
        return f"成员{count_text}人；已知成员：{'、'.join(known)}"

    @staticmethod
    def _render_media(tree: dict[str, Any]) -> str | None:
        status = _optional_string(tree, "cabin.media.play_status")
        app = _optional_string(tree, "cabin.media.app_name")
        artist = _optional_string(tree, "cabin.media.artist")
        title = _optional_string(tree, "cabin.media.title")
        category = _optional_string(tree, "cabin.media.category")
        source_type = _optional_string(tree, "cabin.media.media_source_type")
        volume = _optional_number(tree, "cabin.media.volume")
        sound_zone = _optional_string(tree, "cabin.audio.sound_zone")
        if not any(
            (status, app, artist, title, category, source_type, volume is not None)
        ):
            return None

        if status == "stop" and title is None:
            text = f"{app}未播放媒体" if app else "媒体未播放"
        else:
            subject = ""
            if artist and artist != title:
                subject += f"{artist}的"
            subject += f"《{title}》" if title else "媒体"
            state = _translate(status, _PLAY_STATUS, "media status") if status else ""
            if app and state == "正在播放":
                text = f"{app}正在播放{subject}"
            elif app:
                text = f"{app}中{subject}{state}"
            else:
                text = f"{subject}{state}"

        if category:
            text += f"；类型：{category}"
        elif source_type:
            text += f"；类型：{source_type}"
        if volume is not None:
            text += f"；音量{_number_text(volume)}"
        if sound_zone:
            text += f"；音区：{_translate(sound_zone, _SOUND_ZONE, 'sound zone')}"
        return text

    @staticmethod
    def _render_screen(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        brightness = _optional_number(tree, "cabin.screen.center.brightness_level")
        mode = _optional_string(tree, "cabin.screen.center.content_mode")
        hmi_app = _optional_string(tree, "cabin.screen.hmi.app")
        hmi_name = _optional_string(tree, "cabin.screen.hmi.media_name")
        hmi_status = _optional_string(tree, "cabin.screen.hmi.play_status")
        description = _optional_string(tree, "cabin.screen.hmi.description")
        input_type = _optional_string(tree, "cabin.screen.hmi.last_input_type")
        control_name = _optional_string(tree, "cabin.screen.hmi.last_control_name")
        if brightness is not None:
            parts.append(f"中控屏亮度{_number_text(brightness)}级")
        if mode:
            parts.append(
                f"显示{_translate(mode, _CONTENT_MODE, 'screen content mode')}"
            )
        if hmi_app:
            parts.append(f"HMI应用：{hmi_app}")
        if hmi_name:
            status_text = (
                _translate(hmi_status, _PLAY_STATUS, "HMI play status")
                if hmi_status
                else ""
            )
            parts.append(f"HMI媒体：《{hmi_name}》{status_text}")
        elif hmi_status == "stop":
            parts.append("HMI未播放媒体")
        if control_name:
            input_text = "触控" if input_type == "touch" else input_type
            suffix = f"（{input_text}）" if input_text else ""
            parts.append(f"最近操作：{control_name}{suffix}")
        if description:
            parts.append(f"HMI说明：{description}")
        return "；".join(parts) or None

    @staticmethod
    def _render_navigation(tree: dict[str, Any]) -> str | None:
        active = _optional_bool(tree, "navigation.active")
        start = _optional_string(tree, "navigation.start_poi")
        destination = _optional_string(tree, "navigation.destination")
        distance = _optional_number(tree, "navigation.distance_km")
        eta = _optional_number(tree, "navigation.eta_minutes")
        summary = _optional_string(tree, "navigation.route_summary")
        if active is None and not any((start, destination, distance, eta, summary)):
            return None
        parts = ["导航已开启" if active else "导航未开启"] if active is not None else []
        if summary:
            parts.append(f"路线信息：{summary}")
        else:
            if start:
                parts.append(f"起点：{start}")
            if destination:
                parts.append(f"目的地：{destination}")
            if distance is not None:
                parts.append(f"距离{_number_text(distance)}km")
            if eta is not None:
                parts.append(f"预计{_number_text(eta)}分钟")
        return "；".join(parts) or None

    @staticmethod
    def _render_energy(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        plugged = _optional_bool(tree, "power.charge_port.plug_connected")
        port_status = _optional_string(tree, "power.charge_port.port_status")
        charging = _optional_string(tree, "power.charging.charge_status")
        power = _optional_number(tree, "power.charging.power_kw")
        remaining = _optional_number(tree, "power.charging.remain_min")
        level = _optional_number(tree, "power.traction_battery.soc_pct")
        range_km = _optional_number(tree, "power.traction_battery.range_km")
        threshold = _optional_number(tree, "power.traction_battery.alert_threshold_pct")
        if plugged is not None:
            if plugged and port_status == "locked":
                parts.append("充电口已连接并锁定")
            elif plugged:
                parts.append("充电口已连接")
            elif port_status == "closed":
                parts.append("充电口未连接且已关闭")
            else:
                parts.append("充电口未连接")
        if charging:
            parts.append(
                _translate(
                    charging,
                    {
                        "charging": "正在充电",
                        "idle": "未充电",
                        "stop": "充电已停止",
                        "done": "充电已完成",
                        "fault": "充电故障",
                    },
                    "charging status",
                )
            )
        if power is not None and power > 0:
            parts.append(f"充电功率{_number_text(power)}kW")
        if remaining is not None and remaining > 0:
            parts.append(f"预计剩余{_number_text(remaining)}分钟")
        if level is not None:
            parts.append(f"电量{_number_text(level)}%")
        if range_km is not None:
            parts.append(f"续航{_number_text(range_km)}km")
        if threshold is not None:
            parts.append(f"低电量提醒阈值{_number_text(threshold)}%")
        return "；".join(parts) or None

    @staticmethod
    def _render_schedule(tree: dict[str, Any]) -> str | None:
        schedule = _path(tree, "schedule")
        if schedule is _MISSING:
            return None
        if not isinstance(schedule, dict):
            raise ValueError("schedule must be an object")
        in_meeting = _optional_bool(tree, "schedule.in_meeting")
        starts_in = _optional_number(tree, "schedule.minutes_to_start")
        next_event = _optional_string(tree, "schedule.next_event")
        if in_meeting:
            return f"会议中：{next_event}" if next_event else "会议中；没有下一日程"
        if next_event:
            if starts_in is not None:
                return f"下一日程：{next_event}；{_number_text(starts_in)}分钟后开始"
            return f"下一日程：{next_event}"
        return "没有下一日程"

    @staticmethod
    def _render_climate(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        ac_on = _optional_bool(tree, "hvac.ac_unit.ac_on")
        ac_mode = _optional_string(tree, "hvac.ac_unit.ac_mode")
        fan = _optional_number(tree, "hvac.airflow.fan_speed")
        airflow = _optional_string(tree, "hvac.airflow.airflow_mode")
        target_all = _optional_number(tree, "hvac.zone.all.target_temp_c")
        target_left = _optional_number(tree, "hvac.zone.front_left.target_temp_c")
        target_right = _optional_number(tree, "hvac.zone.front_right.target_temp_c")
        if ac_on is not None:
            parts.append("空调开启" if ac_on else "空调关闭")
        if ac_mode:
            parts.append(f"{_translate(ac_mode, _AC_MODE, 'AC mode')}模式")
        if fan is not None:
            parts.append(f"风量{_number_text(fan)}档")
        if airflow:
            parts.append(f"向{_translate(airflow, _AIRFLOW_MODE, 'airflow mode')}出风")
        if target_all is not None:
            parts.append(f"目标温度{_number_text(target_all)}℃")
        if target_left is not None and target_left != target_all:
            parts.append(f"主驾目标温度{_number_text(target_left)}℃")
        if target_right is not None and target_right != target_all:
            parts.append(f"副驾目标温度{_number_text(target_right)}℃")
        return "；".join(parts) or None

    @staticmethod
    def _render_windows(tree: dict[str, Any]) -> str | None:
        reported: list[tuple[str, int | float]] = []
        for path, name in _WINDOW_NAMES.items():
            value = _optional_number(tree, path)
            if value is not None:
                reported.append((name, value))
        if not reported:
            return None
        opened = [
            f"{name}开启{_number_text(value)}%" for name, value in reported if value > 0
        ]
        closed_count = sum(value == 0 for _, value in reported)
        if not opened:
            return "已上报车窗均关闭"
        if closed_count:
            opened.append("其他已上报车窗关闭")
        return "；".join(opened)

    @staticmethod
    def _render_seats(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        for path, name in _VENTILATION_NAMES.items():
            value = _optional_number(tree, path)
            if value is None:
                continue
            if value == 0:
                parts.append(f"{name}通风关闭")
            else:
                parts.append(f"{name}通风{_number_text(value)}档")
        return "；".join(parts) or None

    @staticmethod
    def _render_dialogue(tree: dict[str, Any]) -> str | None:
        parts: list[str] = []
        dnd = _optional_bool(tree, "cabin.dialogue.do_not_disturb")
        speaker = _optional_string(tree, "cabin.dialogue.speaker_seat")
        if dnd is not None:
            parts.append("勿扰模式开启" if dnd else "勿扰模式关闭")
        if speaker:
            speaker_label = {
                "seat.row1.left": "主驾",
                "seat.row1.right": "副驾",
                "seat.row2.left": "二排左座",
                "seat.row2.right": "二排右座",
                "seat.row3.left": "三排左座",
                "seat.row3.middle": "三排中座",
                "seat.row3.right": "三排右座",
            }.get(speaker)
            if speaker_label is None:
                raise ValueError(f"unsupported speaker seat: {speaker!r}")
            parts.append(f"说话成员：{speaker_label}")
        return "；".join(parts) or None
