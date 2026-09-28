"""Strict field contract for the canonical vehicle device protocol."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

from pydantic import JsonValue

_MISSING = object()

FieldKind = Literal["bool", "enum", "float", "int", "str"]


@dataclass(frozen=True)
class FieldRule:
    kind: FieldKind
    label: str
    minimum: float | None = None
    maximum: float | None = None
    values: frozenset[str] = frozenset()
    unit: str = ""

    def validate(self, path: str, value: JsonValue) -> None:
        if self.kind in {"bool", "float", "int"} and value is None:
            return
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"invalid boolean for {path!r}: {value!r}")
            return
        if self.kind in {"float", "int"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid number for {path!r}: {value!r}")
            if not isfinite(value):
                raise ValueError(f"invalid number for {path!r}: {value!r}")
            if self.kind == "int" and not isinstance(value, int):
                raise ValueError(f"invalid integer for {path!r}: {value!r}")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"invalid number for {path!r}: {value!r}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"invalid number for {path!r}: {value!r}")
            return
        if not isinstance(value, str):
            raise ValueError(f"invalid string for {path!r}: {value!r}")
        if self.kind == "enum" and value not in self.values:
            raise ValueError(f"unsupported value for {path!r}: {value!r}")


def _bool(label: str) -> FieldRule:
    return FieldRule("bool", label)


def _enum(label: str, values: str) -> FieldRule:
    return FieldRule("enum", label, values=frozenset((*values.split("/"), "")))


def _float(
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
    unit: str = "",
) -> FieldRule:
    return FieldRule("float", label, minimum, maximum, unit=unit)


def _int(
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
    unit: str = "",
) -> FieldRule:
    return FieldRule("int", label, minimum, maximum, unit=unit)


def _str(label: str) -> FieldRule:
    return FieldRule("str", label)


FIELD_RULES: dict[str, FieldRule] = {}


def _add(prefix: str, fields: dict[str, FieldRule]) -> None:
    for name, rule in fields.items():
        path = f"{prefix}.{name}"
        if path in FIELD_RULES:
            raise RuntimeError(f"duplicate vehicle protocol path: {path}")
        FIELD_RULES[path] = rule


_WHEELS = ("front_left", "front_right", "rear_left", "rear_right")
_WINDOWS = ("row1.left", "row1.right", "row2.left", "row2.right")
_DOORS = _WINDOWS
_SUNSHADES = ("front", "rear", "all")
_MIRRORS = ("left", "right")
_WIRELESS_CHARGERS = ("row1.left", "row1.right")
_HVAC_ZONES = {
    "front_left": "主驾",
    "front_right": "副驾",
    "rear": "后排",
    "all": "全车",
}
SEAT_NODES = (
    "seat.row1.left",
    "seat.row1.right",
    "seat.row2.left",
    "seat.row2.right",
    "seat.row3.left",
    "seat.row3.middle",
    "seat.row3.right",
)
_READING_LIGHTS = (
    "row1.left",
    "row1.right",
    "row2.left",
    "row2.right",
    "row3.left",
    "row3.right",
)
SCREEN_NODES = (
    "cabin.screen.hud",
    "cabin.screen.instrument",
    "cabin.screen.center",
    "cabin.screen.copilot",
    "cabin.screen.rear_entertainment",
    "cabin.screen.touchbar",
)

_add(
    "chassis.powertrain",
    {
        "speed_kmh": _int("车速", 0, 260, "km/h"),
        "gear": _enum("挡位", "P/R/N/D"),
        "gear_selector": _enum("挡位选择", "P/R/N/D"),
        "ignition": _enum("上电状态", "off/acc/ready"),
        "energy_recovery_mode": _enum(
            "能量回收模式", "low/standard/high/auto/unknown"
        ),
        "energy_recovery_level": _int("能量回收等级", 0, 3),
    },
)
_add(
    "chassis.drive_mode",
    {
        "drive_mode": _enum(
            "驾驶模式", "comfort/sport/eco/snow/offroad/custom"
        ),
        "scene_mode": _enum(
            "场景模式",
            "none/nap/camping/carWash/cleaning/tow/maintenance/theater/custom",
        ),
    },
)
_add("chassis.parking_brake", {"on": _bool("电子驻车")})
_add(
    "chassis.steering",
    {
        "angle_deg": _int("方向盘角度", -720, 720, "度"),
        "torque_nm": _float("方向盘扭矩", unit="Nm"),
    },
)
_add(
    "chassis.brake",
    {
        "pedal_pct": _float("制动踏板开度", 0, 100, "%"),
        "pressure_bar": _float("制动压力", 0, unit="bar"),
    },
)
_add(
    "chassis.accelerator",
    {"pedal_pct": _float("加速踏板开度", 0, 100, "%")},
)
_add(
    "chassis.dynamics",
    {
        "yaw_rate": _float("横摆角速度", -120, 120),
        "long_accel": _float("纵向加速度", -8, 6),
        "lat_accel": _float("横向加速度", -6, 6),
    },
)
_add(
    "chassis.suspension",
    {
        "height_mode": _enum("悬架高度模式", "low/normal/high/loading"),
        "height_mm": _float("悬架高度", unit="mm"),
    },
)
for wheel in _WHEELS:
    _add(
        f"chassis.wheel.{wheel}",
        {
            "pressure_kpa": _float("胎压", 180, 320, "kPa"),
            "temp_c": _float("胎温", -40, 120, "℃"),
        },
    )

_add(
    "power.traction_battery",
    {
        "soc_pct": _float("动力电池电量", 0, 100, "%"),
        "alert_threshold_pct": _float("低电量阈值", 0, 100, "%"),
        "temp_c": _float("动力电池温度", -30, 70, "℃"),
        "range_km": _float("综合续航", 0, 1200, "km"),
        "wltc_electric_range_km": _float("WLTC纯电续航", 0, unit="km"),
        "cltc_electric_range_km": _float("CLTC纯电续航", 0, unit="km"),
        "wltc_fuel_range_km": _float("WLTC燃油续航", 0, unit="km"),
        "cltc_fuel_range_km": _float("CLTC燃油续航", 0, unit="km"),
        "odometer_km": _float("总里程", 0, unit="km"),
    },
)
_add(
    "power.charging",
    {
        "charge_status": _enum(
            "充电状态", "idle/charging/stop/done/fault"
        ),
        "remain_min": _int("剩余充电时间", 0, unit="分钟"),
        "voltage_v": _float("充电电压", 0, unit="V"),
        "current_a": _float("充电电流", 0, unit="A"),
        "power_kw": _float("充电功率", 0, unit="kW"),
        "charge_limit_pct": _int("充电上限", 0, 100, "%"),
    },
)
_add(
    "power.charge_port",
    {
        "port_status": _enum("充电口状态", "open/closed/locked/fault"),
        "plug_connected": _bool("充电枪连接"),
    },
)
for charger in _WIRELESS_CHARGERS:
    _add(
        f"power.wireless_charger.{charger}",
        {
            "request": _bool("无线充电请求"),
            "status": _enum(
                "无线充电状态", "off/standby/charging/full/fault/foreignObject"
            ),
        },
    )
_add("power.low_voltage_battery", {"soc_pct": _float("低压电池电量", 0, 100, "%")})
_add(
    "power.thermal",
    {
        "preheat_on": _bool("电池预热"),
        "keep_warm_on": _bool("电池保温"),
        "cabin_precondition_on": _bool("座舱预调节"),
    },
)
_add(
    "power.scheduled_departure",
    {"enabled": _bool("按时出发"), "time": _str("出发时间")},
)
_add(
    "power.consumption",
    {
        "daily": _float("日能耗", 0, unit="kWh/100km"),
        "monthly": _float("月能耗", 0, unit="kWh/100km"),
        "last_trip": _float("上次行程能耗", 0, unit="kWh/100km"),
    },
)
_add("power.external_discharge", {"on": _bool("对外放电")})

_add("body.window", {"auto_close_on_lock": _bool("锁车自动关窗")})
for window in _WINDOWS:
    _add(f"body.window.{window}", {"open_pct": _int("车窗开度", 0, 100, "%")})
_add("body.sunroof", {"open_pct": _int("天窗开度", 0, 100, "%")})
for sunshade in _SUNSHADES:
    _add(f"body.sunshade.{sunshade}", {"open_pct": _int("遮阳帘开度", 0, 100, "%")})
for door in _DOORS:
    _add(
        f"body.door.{door}",
        {"open": _bool("车门开启"), "locked": _bool("车门锁定")},
    )
_add("body.trunk", {"open": _bool("后备厢开启")})
_add("body.hood", {"open": _bool("前机舱盖开启")})
_add("body.central_lock", {"on": _bool("中控锁")})
for side in _MIRRORS:
    _add(f"body.child_lock.{side}", {"on": _bool("儿童锁")})
    _add(
        f"body.mirror.{side}",
        {"folded": _bool("后视镜折叠"), "heating_on": _bool("后视镜加热")},
    )
_add(
    "body.mirror",
    {
        "auto_dimming": _bool("后视镜自动防眩"),
        "auto_fold_on_lock": _bool("锁车自动折叠后视镜"),
        "electronic": _bool("电子后视镜"),
    },
)
_add(
    "body.access_function",
    {
        "walk_away_lock": _bool("离车闭锁"),
        "welcome_mode": _bool("迎宾模式"),
        "anti_pinch": _bool("防夹"),
    },
)

_add(
    "hvac.ac_unit",
    {
        "ac_on": _bool("空调"),
        "compressor_on": _bool("压缩机"),
        "ac_mode": _enum("空调模式", "auto/manual/eco/defrost/rest"),
        "sync_on": _bool("多区温度同步"),
        "auto_dry_on": _bool("自动干燥"),
        "rear_ac_auto_close": _bool("后空调自动关闭"),
        "manual_fan_control_req": _bool("前排手动风速请求"),
    },
)
for zone, label in _HVAC_ZONES.items():
    _add(
        f"hvac.zone.{zone}",
        {"target_temp_c": _float(f"{label}目标温度", 16, 30, "℃")},
    )
_add(
    "hvac.airflow",
    {
        "fan_speed": _int("风量", 0, 7, "档"),
        "airflow_mode": _enum(
            "总出风模式", "face/foot/defrost/face_foot/auto"
        ),
        "front_outlet_mode": _enum(
            "前排出风模式", "face/foot/defrost/face_foot/auto"
        ),
        "rear_outlet_mode": _enum(
            "后排出风模式", "face/foot/defrost/face_foot/auto"
        ),
        "front_direction": _enum("前排吹风方向", "up/middle/down/auto"),
        "rear_direction": _enum("后排吹风方向", "up/middle/down/auto"),
    },
)
_add(
    "hvac.recirculation",
    {
        "mode": _enum("循环模式", "inner/outer/auto"),
        "recirc_on": _bool("内循环"),
        "fresh_air_on": _bool("新风"),
    },
)
_add(
    "hvac.defrost",
    {
        "front_on": _bool("前除霜"),
        "rear_on": _bool("后除霜"),
        "auto_defog_on": _bool("自动除雾"),
    },
)
_add(
    "hvac.air_purifier",
    {
        "purifier_on": _bool("空气净化"),
        "purifier_running": _bool("空气净化运行"),
        "aqs_enabled": _bool("AQS"),
        "negative_ion_on": _bool("负离子"),
        "pm25_request": _bool("PM2.5净化请求"),
    },
)
_add(
    "hvac.fragrance",
    {
        "on": _bool("香氛"),
        "type": _str("香氛类型"),
        "intensity": _int("香氛强度", 0, 3),
    },
)
_add(
    "hvac.refrigerator",
    {
        "on": _bool("冰箱"),
        "temp_c": _float("冰箱温度", -6, 50, "℃"),
        "mode": _enum("冰箱模式", "cool/warm/scene/off"),
    },
)

_add("seat", {"passenger_count": _int("乘员数量", 0, 7, "人")})
_add(
    "seat.row_comfort_modes",
    {
        row: _enum(f"{row}舒适模式", "commute/rest/cinema/nap/custom")
        for row in ("row1", "row2", "row3")
    },
)
_SEAT_SLOT_FIELDS = {
    "available": _bool("座椅可用"),
    "occupied": _bool("座椅占用"),
    "occupancy_confidence": _float("占用置信度", 0, 1),
    "person_id": _str("乘员ID"),
    "user_id": _str("用户ID"),
    "occupant_class": _enum(
        "乘员类型", "adult/child/infant/pet/object/unknown"
    ),
    "weight_kg": _float("估计体重", 0, 180, "kg"),
    "fore_aft_mm": _int("座椅前后位置", -260, 260, "mm"),
    "height_mm": _int("座椅高度", -80, 80, "mm"),
    "backrest_angle_deg": _int("靠背角度", 10, 70, "度"),
    "cushion_tilt_deg": _int("坐垫角度", -10, 20, "度"),
    "lumbar_level": _int("腰托等级", 0, 3),
    "armrest_deployed": _bool("扶手展开"),
    "leg_rest_pct": _int("腿托开度", 0, 100, "%"),
    "heater_level": _int("座椅加热", 0, 3, "档"),
    "ventilation_level": _int("座椅通风", 0, 3, "档"),
    "massage_level": _int("按摩强度", 0, 3, "档"),
    "massage_mode": _enum(
        "按摩模式", "off/relax/stretch/waist/shoulder/custom"
    ),
    "seat_temperature_c": _float("座椅表面温度", -20, 60, "℃"),
    "cushion_pressure_kpa": _float("坐垫压力", 0, unit="kPa"),
    "backrest_pressure_kpa": _float("靠背压力", 0, unit="kPa"),
    "belt_latched": _bool("安全带系上"),
    "child_seat_detected": _bool("儿童座椅识别"),
    "child_seat_linked": _bool("儿童座椅联动"),
    "table_deployed": _bool("小桌板展开"),
    "vibration_4d_level": _int("4D振动", 0, 3, "档"),
}
for seat_node in SEAT_NODES:
    _add(seat_node, _SEAT_SLOT_FIELDS)

_add(
    "lighting.exterior",
    {
        "headlight_mode": _enum("车灯模式", "off/auto/position/low/high"),
        "low_beam": _bool("近光灯"),
        "high_beam": _bool("远光灯"),
        "smart_low": _bool("智能近光"),
        "smart_high": _bool("智能远光"),
        "afs_low": _bool("AFS随动近光"),
        "position": _bool("位置灯"),
        "fog_front": _bool("前雾灯"),
        "fog_rear": _bool("后雾灯"),
        "turn_signal": _enum("转向灯", "off/left/right"),
        "hazard": _bool("双闪"),
        "brake": _bool("制动灯"),
        "reverse": _bool("倒车灯"),
        "welcome": _bool("迎宾灯"),
        "follow_me_home": _bool("伴我回家灯"),
    },
)
_add(
    "lighting.ambient",
    {
        "ambient_on": _bool("氛围灯"),
        "ambient_color": _str("氛围灯颜色"),
        "ambient_brightness": _float("氛围灯亮度", 0, 1),
        "star_ring_on": _bool("星环灯"),
    },
)
_add(
    "lighting.interior",
    {
        "interior_on": _bool("车内灯"),
        "footwell_on": _bool("照脚灯"),
        "armrest_box_on": _bool("扶手箱灯"),
    },
)
for light in _READING_LIGHTS:
    _add(
        f"lighting.reading.{light}",
        {"on": _bool("阅读灯"), "brightness": _int("阅读灯亮度", 0, 100, "%")},
    )

_add(
    "wiper",
    {
        "front_mode": _enum("前雨刮模式", "off/auto/once/low/high"),
        "rear_mode": _enum("后雨刮模式", "off/auto/once/low/high"),
        "washer_active": _bool("洗涤功能"),
        "rain_sensing_on": _bool("感应雨刮"),
    },
)

_SCREEN_FIELDS = {
    "available": _bool("屏幕可用"),
    "on": _bool("屏幕"),
    "content_mode": _enum(
        "屏幕内容",
        "idle/home/navigation/media/climate/charging/settings/camera/seat/game/kids/video/driving",
    ),
    "brightness_level": _int("屏幕亮度", 0, 10, "级"),
    "source": _enum("屏幕来源", "system/vehicle/media/phone/hdmi/usb/unknown"),
}
for screen_node in SCREEN_NODES:
    _add(screen_node, _SCREEN_FIELDS)
_add(
    "cabin.screen.rear_entertainment",
    {
        "deployed": _bool("后排屏展开"),
        "deployed_pct": _int("后排屏展开比例", 0, 100, "%"),
        "angle_deg": _int("后排屏角度", 0, 180, "度"),
    },
)
_add(
    "cabin.screen.hmi",
    {
        "active_screen": _str("活动屏幕"),
        "app": _str("HMI应用"),
        "media_name": _str("HMI媒体"),
        "play_status": _enum("HMI播放状态", "playing/pause/stop"),
        "collect_status": _bool("收藏状态"),
        "description": _str("HMI说明"),
        "last_touch": _str("最近触摸输入"),
        "last_input_type": _enum(
            "最近输入类型", "touch/voice/button/touchbar/gesture/unknown"
        ),
        "last_control_name": _str("最近控件"),
        "touchbar_active": _bool("Touch Bar活动"),
        "visible_speech_enabled": _bool("可见即可说"),
    },
)
_add(
    "cabin.audio",
    {
        "sound_zone": _enum("音区", "all/driver/front/rear/row2/row3"),
        "spatial_mode": _enum("空间音效", "standard/spatial/cinema/focus"),
        "dolby_atmos": _bool("杜比全景声"),
        "equalizer_mode": _enum("均衡器模式", "standard/pop/voice/bass/custom"),
        "brand": _str("音响品牌"),
        "layout": _str("声道布局"),
        "speed_vol_comp": _bool("车速音量补偿"),
        "audio_focus_mode": _enum(
            "音频焦点模式", "normal/driver/rear/karaoke/cinema"
        ),
    },
)
_add(
    "cabin.media",
    {
        "app_name": _str("媒体应用"),
        "package_name": _str("媒体应用包名"),
        "media_source_type": _enum(
            "媒体来源", "music/audio/video/radio/podcast/bluetooth/local/streaming/none"
        ),
        "title": _str("媒体标题"),
        "artist": _str("艺术家"),
        "album": _str("专辑"),
        "episode": _int("当前集数", 0),
        "total_episode": _int("总集数", 0),
        "category": _str("媒体分类"),
        "play_status": _enum("媒体播放状态", "playing/pause/stop/buffering"),
        "play_time": _str("播放时间"),
        "volume": _int("媒体音量", 0, 100),
        "muted": _bool("媒体静音"),
    },
)
_add(
    "cabin.dialogue",
    {
        "query": _str("用户请求"),
        "tts": _str("车机播报"),
        "speaker_seat": _str("说话座位"),
        "domain": _enum(
            "对话领域", "导航/媒体/车辆控制/天气/问答/电话/内容查询/其他"
        ),
        "request_time": _str("请求时间"),
        "wake_zone": _enum(
            "唤醒区域", "front/rear/driver/passenger/row2.left/row2.right/unknown"
        ),
        "barge_in": _bool("打断播报"),
        "voice_volume": _int("播报音量", 0, 100),
        "do_not_disturb": _bool("勿扰模式"),
        "interaction_mode": _enum("交互方式", "voice/touch/gesture/auto"),
    },
)
_add(
    "cabin.dialogue.mic_array",
    {
        "channels": _int("麦克风通道数", 0),
        "azimuth_deg": _float("声源方位角", -180, 180, "度"),
    },
)
_OCCUPANT_EMOTION = "高兴/平静/焦虑/生气/疲惫/其他"
_OCCUPANT_ACTION = "说话/看屏幕/看窗外/使用手机/睡觉/未说话/其他"
for role in ("driver", "passenger"):
    _add(
        f"cabin.occupancy.{role}",
        {
            "emotion": _enum("乘员情绪", _OCCUPANT_EMOTION),
            "action": _enum("乘员动作", _OCCUPANT_ACTION),
            "description": _str("乘员摘要"),
        },
    )
_add("cabin.occupancy.rear", {"description": _str("后排摘要")})
_add(
    "cabin.cabin_env",
    {
        "noise_db": _int("座舱噪声", unit="dB"),
        "cabin_temp_c": _float("座舱温度", -20, 60, "℃"),
        "front_cabin_temp_c": _float("前排温度", -20, 60, "℃"),
        "rear_cabin_temp_c": _float("后排温度", -20, 60, "℃"),
        "pm25": _float("车内PM2.5", 0, 1000),
        "co2": _float("车内CO2", 350, 5000, "ppm"),
        "voc": _float("车内VOC", 0, unit="ppb"),
        "aqi": _float("车内AQI", 0),
    },
)

_add(
    "sensing.dms_camera",
    {
        "detected": _bool("检测到驾驶员"),
        "gaze_target": _enum(
            "视线目标", "road/center_screen/mirror/phone/passenger/window/unknown"
        ),
        "gaze_confidence": _float("视线置信度", 0, 1),
        "hands_on_wheel": _bool("手握方向盘"),
        "distraction_score": _float("分心分数", 0, 1),
        "fatigue_score": _float("疲劳分数", 0, 1),
        "eye_closure_pct": _float("眼睛闭合比例", 0, 1),
        "head_yaw_deg": _float("头部航向角", unit="度"),
        "head_pitch_deg": _float("头部俯仰角", unit="度"),
        "head_roll_deg": _float("头部滚转角", unit="度"),
        "yawn": _bool("打哈欠"),
        "phone_use": _bool("使用手机"),
        "left_hand_state": _enum(
            "左手状态", "unknown/onWheel/onPhone/onScreen/free"
        ),
        "right_hand_state": _enum(
            "右手状态", "unknown/onWheel/onPhone/onScreen/free"
        ),
        "left_hand_contact": _enum(
            "左手接触对象", "steeringWheel/phone/screen/other"
        ),
        "right_hand_contact": _enum(
            "右手接触对象", "steeringWheel/phone/screen/other"
        ),
        "face_occlusion_pct": _float("脸部遮挡比例", 0, 1),
        "eye_occlusion_pct": _float("眼部遮挡比例", 0, 1),
        "emotion": _enum("驾驶员情绪", "calm/happy/anxious/angry/tired/unknown"),
        "posture": _enum("驾驶员姿态", "normal/leaning/lookingDown/sleepy/unknown"),
    },
)
_add(
    "sensing.cabin_camera.camera",
    {
        "enabled": _bool("座舱摄像头"),
        "privacy": _bool("摄像头隐私"),
        "type": _enum("摄像头类型", "rgb/ir/depth/fisheye"),
        "mount": _str("摄像头安装位置"),
        "occlusion_level": _float("摄像头遮挡程度", 0, 1),
        "occlusion_reason": _enum(
            "摄像头遮挡原因", "none/hand/object/dust/privacy/unknown"
        ),
    },
)
_RISK_VALUES = "none/low/medium/high"
_add(
    "sensing.cabin_camera",
    {
        "child_left_behind_risk": _enum("儿童遗留风险", _RISK_VALUES),
        "pet_left_behind_risk": _enum("宠物遗留风险", _RISK_VALUES),
        "object_left_behind_risk": _enum("物品遗留风险", _RISK_VALUES),
    },
)
_add("sensing.env.outside_temp", {"temp_c": _float("车外温度", -40, 55, "℃")})
_add(
    "sensing.env.outside_humidity",
    {"humidity_pct": _float("车外湿度", 0, 100, "%")},
)
_add(
    "sensing.env.outside_air_quality",
    {
        "aqi": _float("车外AQI", 0),
        "pm25": _float("车外PM2.5", 0, 1000),
        "voc": _float("车外VOC", 0),
    },
)
_add("sensing.env.wind", {"speed_mps": _float("风速", 0, unit="m/s")})
_add(
    "sensing.env.light",
    {"illuminance_lux": _float("光照强度", 0, 120000, "lux")},
)
_add("sensing.env.rain", {"intensity_mmh": _float("降雨强度", 0, 80, "mm/h")})
_add(
    "sensing.env.fog",
    {
        "fog_level": _float("环境雾气", 0, 1),
        "windshield_fog_level": _float("前挡起雾", 0, 1),
    },
)
_add(
    "sensing.env.sun",
    {
        "solar_radiance_wm2": _float("太阳辐照", 0, unit="W/m²"),
        "azimuth_deg": _float("太阳方位角", 0, 360, "度"),
        "elevation_deg": _float("太阳高度角", -90, 90, "度"),
        "uv_index": _float("紫外线指数", 0),
    },
)
_add(
    "sensing.location",
    {
        "geo_type": _enum(
            "地理类型",
            "road/highway/bridge/tunnel/charger/parking/home/office/mall/hospital/school/restaurant/hotel/other",
        ),
        "poi_name": _str("位置"),
    },
)

_add(
    "safety.active_safety",
    {
        "lka_frequency": _int("车道保持触发次数", 0),
        "low_speed_ped_warning": _bool("低速行人提示音"),
        "abs_active": _bool("ABS触发"),
        "abs_fault": _bool("ABS故障"),
        "esc_enabled": _bool("ESC开启"),
        "esc_active": _bool("ESC触发"),
        "esc_fault": _bool("ESC故障"),
        "aeb": _bool("AEB"),
        "fcw": _bool("FCW"),
        "lka": _bool("LKA"),
        "ldw": _bool("LDW"),
        "blind_spot": _bool("盲区辅助"),
    },
)
_add(
    "safety.passive_safety",
    {
        "airbag_status": _enum("安全气囊状态", "ok/fault/deployed/unknown"),
        "belt_pretensioner_status": _enum(
            "安全带预紧器状态", "inactive/active/fault/unknown"
        ),
    },
)
_add(
    "safety.adas",
    {
        name: _bool(label)
        for name, label in {
            "noa": "NOA",
            "parking_assist": "泊车辅助",
            "remote_parking": "遥控泊车",
            "door_open_warning": "门开预警",
            "panoramic_camera": "全景影像",
        }.items()
    },
)
_add("safety.dashcam", {"recording": _bool("行车记录仪录像")})
_add("safety.emergency_call", {"available": _bool("紧急呼叫可用")})

_add(
    "connectivity.bluetooth",
    {
        "on": _bool("蓝牙"),
        "connected": _bool("蓝牙连接"),
        "device_name": _str("蓝牙设备"),
    },
)
_add(
    "connectivity.phone",
    {
        "call_status": _enum("电话状态", "idle/ringing/dialing/inCall/ended"),
        "contact_name": _str("联系人"),
        "number_masked": _str("脱敏号码"),
    },
)
_add(
    "connectivity.network",
    {
        "network_status": _enum("网络状态", "offline/online/weak/unknown"),
        "hotspot_on": _bool("车载热点"),
    },
)
_add(
    "connectivity.screencast",
    {
        "on": _bool("投屏"),
        "source": _enum("投屏来源", "phone/hdmi/usb/unknown"),
    },
)
_add("connectivity.usb", {"connected": _bool("USB连接")})
_add(
    "connectivity.key",
    {
        "digital_key_status": _enum(
            "数字钥匙状态", "none/detected/authorized/invalid"
        ),
        "remote_key_detected": _bool("遥控钥匙检测"),
    },
)

_add(
    "navigation",
    {
        "active": _bool("导航"),
        "destination": _str("目的地"),
        "start_poi": _str("起点"),
        "cost_time_min": _int("导航时长", 0, unit="分钟"),
        "eta_minutes": _int("剩余导航时间", 0, unit="分钟"),
        "distance_km": _float("剩余距离", 0, unit="km"),
        "next_maneuver": _enum(
            "下一步动作", "直行/左转/右转/掉头/进匝道/靠左/靠右/到达"
        ),
        "route_preference": _enum(
            "路线偏好", "fastest/avoidTolls/avoidCongestion/comfort"
        ),
        "destination_type": _enum(
            "目的地类型",
            "home/office/charger/parking/mall/hospital/school/restaurant/hotel/other",
        ),
        "start_time": _str("导航开始时间"),
        "end_time": _str("导航结束时间"),
        "route_summary": _str("路线摘要"),
    },
)
_add(
    "schedule",
    {
        "next_event": _str("下一日程"),
        "minutes_to_start": _int("日程开始倒计时", 0, unit="分钟"),
        "in_meeting": _bool("会议中"),
    },
)
_add("maintenance.washer_fluid", {"low": _bool("玻璃水不足")})
_add("maintenance.air_filter", {"life_pct": _float("空调滤芯寿命", 0, 100, "%")})
_add(
    "maintenance.ota",
    {"status": _enum("OTA状态", "none/available/downloading/installing/completed/failed")},
)
_add(
    "maintenance.health",
    {
        "camera": _enum("摄像头健康", "ok/degraded/offline/blocked"),
        "radar": _enum("雷达健康", "ok/degraded/offline/blocked"),
        "mic": _enum("麦克风健康", "ok/degraded/offline/blocked"),
        "ac": _enum("空调健康", "ok/degraded/fault/unknown"),
        "battery": _enum("电池健康", "ok/degraded/fault/unknown"),
    },
)

ARRAY_PATHS = frozenset(
    {
        "seat.slot_order",
        "cabin.dialogue.passengers",
        "sensing.cabin_camera.objects",
    }
)

def _path_prefixes(paths: set[str]) -> frozenset[str]:
    prefixes: set[str] = set()
    for path in paths:
        parts = path.split(".")
        prefixes.update(".".join(parts[:index]) for index in range(1, len(parts)))
    return frozenset(prefixes)


DEVICE_NODES = _path_prefixes(set(FIELD_RULES) | set(ARRAY_PATHS))

EVENT_LABELS = {
    "NavigationEvent": "导航状态发生变化",
    "MediaEvent": "媒体状态发生变化",
    "DialogueEvent": "对话状态发生变化",
    "ConnectivityPhoneEvent": "连接或电话状态发生变化",
    "OccupancyEvent": "乘员状态发生变化",
    "ScreenEvent": "屏幕状态发生变化",
    "VehicleMotionEvent": "车辆运动状态发生变化",
    "EnergyChargingEvent": "充电与电量状态发生变化",
    "BodyAccessEvent": "车身控制状态发生变化",
    "SeatComfortEvent": "座椅舒适状态发生变化",
    "ClimateAirEvent": "空调状态发生变化",
    "LightingWiperEvent": "灯光或雨刮状态发生变化",
    "PerceptionSafetyEvent": "感知或安全状态发生变化",
    "EnvironmentLocationEvent": "环境与位置状态发生变化",
    "MaintenanceEvent": "维护状态发生变化",
    "ScheduleEvent": "日程状态发生变化",
}

EVENT_DEVICE_DOMAINS = {
    "NavigationEvent": ("navigation",),
    "MediaEvent": ("cabin.media", "cabin.audio"),
    "DialogueEvent": ("cabin.dialogue",),
    "ConnectivityPhoneEvent": ("connectivity",),
    "OccupancyEvent": ("cabin.occupancy", "seat"),
    "ScreenEvent": ("cabin.screen",),
    "VehicleMotionEvent": ("chassis",),
    "EnergyChargingEvent": ("power",),
    "BodyAccessEvent": ("body",),
    "SeatComfortEvent": ("seat",),
    "ClimateAirEvent": ("hvac",),
    "LightingWiperEvent": ("lighting", "wiper"),
    "PerceptionSafetyEvent": ("sensing", "safety"),
    "EnvironmentLocationEvent": (
        "sensing.env",
        "sensing.location",
        "cabin.cabin_env",
    ),
    "MaintenanceEvent": ("maintenance", "chassis.wheel"),
    "ScheduleEvent": ("schedule",),
}

ACTOR_LABELS = {
    "driver": "驾驶员",
    "passenger": "副驾乘员",
    "rear": "后排乘员",
    "system": "系统",
    "agent": "助手",
    "unknown": "未知主体",
}

TRIGGER_SOURCES = frozenset(
    {
        "导航记录",
        "媒体播放",
        "舱内摄像头",
        "屏幕",
        "车机对话",
        "车辆状态",
        "环境感知",
        "助手",
        "查询结果",
    }
)


def _validate_seat_node(value: JsonValue, path: str, *, allow_empty: bool) -> None:
    accepted = set(SEAT_NODES)
    if allow_empty:
        accepted.add("")
    if not isinstance(value, str) or value not in accepted:
        raise ValueError(f"invalid seat node for {path!r}: {value!r}")


def _validate_screen_node(value: JsonValue, path: str) -> None:
    if not isinstance(value, str) or value not in {*SCREEN_NODES, ""}:
        raise ValueError(f"invalid screen node for {path!r}: {value!r}")


def _validate_array(path: str, value: JsonValue) -> None:
    if not isinstance(value, list):
        raise ValueError(f"invalid array for {path!r}: {value!r}")
    if path == "seat.slot_order":
        if value != list(SEAT_NODES):
            raise ValueError("seat.slot_order must contain the seven canonical seats")
        return
    if path == "cabin.dialogue.passengers":
        for passenger in value:
            _validate_seat_node(passenger, path, allow_empty=False)
        return
    expected_keys = {
        "object_id",
        "object_class",
        "seat",
        "confidence",
        "held_by",
    }
    object_classes = {
        "person",
        "pet",
        "bag",
        "phone",
        "cup",
        "childSeat",
        "object",
        "unknown",
    }
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError(f"{item_path} must contain exactly {sorted(expected_keys)}")
        if not isinstance(item["object_id"], str):
            raise ValueError(f"{item_path}.object_id must be a string")
        if item["object_class"] not in object_classes:
            raise ValueError(f"unsupported {item_path}.object_class")
        _validate_seat_node(item["seat"], f"{item_path}.seat", allow_empty=True)
        _float("对象置信度", 0, 1).validate(
            f"{item_path}.confidence", item["confidence"]
        )
        _validate_seat_node(
            item["held_by"], f"{item_path}.held_by", allow_empty=True
        )


def flatten_and_validate(
    data: dict[str, JsonValue],
    *,
    location: str,
) -> dict[str, JsonValue]:
    leaves: dict[str, JsonValue] = {}

    def visit(path: str, value: JsonValue) -> None:
        if isinstance(value, dict):
            if path and path not in DEVICE_NODES:
                raise ValueError(f"unsupported {location} path: {path}")
            for name, child in value.items():
                if not isinstance(name, str) or not name:
                    raise ValueError(f"{location} field names must not be empty")
                visit(f"{path}.{name}" if path else name, child)
            return
        if isinstance(value, list):
            if path not in ARRAY_PATHS:
                raise ValueError(f"unsupported {location} path: {path}")
            _validate_array(path, value)
            leaves[path] = deepcopy(value)
            return
        try:
            rule = FIELD_RULES[path]
        except KeyError as error:
            raise ValueError(f"unsupported {location} path: {path}") from error
        rule.validate(path, value)
        if path == "cabin.dialogue.speaker_seat":
            _validate_seat_node(value, path, allow_empty=True)
        elif path == "cabin.screen.hmi.active_screen":
            _validate_screen_node(value, path)
        leaves[path] = value

    visit("", data)
    return leaves


def validate_trigger_payload(
    payload: dict[str, JsonValue],
    *,
    event_type: str,
    device_tree: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if event_type == "AgentEvent":
        raise ValueError("AgentEvent cannot trigger an input Record")
    try:
        allowed_domains = EVENT_DEVICE_DOMAINS[event_type]
    except KeyError as error:
        raise ValueError(f"unsupported event type: {event_type!r}") from error
    if len(payload) != 1:
        raise ValueError("trigger payload must contain exactly one device subtree")
    root, subtree = next(iter(payload.items()))
    if not isinstance(subtree, dict) or not subtree:
        raise ValueError("trigger payload root must contain a non-empty object")
    if not any(root == domain or root.startswith(f"{domain}.") for domain in allowed_domains):
        raise ValueError(
            f"trigger payload root {root!r} is not allowed for {event_type}"
        )
    payload_leaves = flatten_and_validate(
        payload,
        location="trigger payload",
    )
    if not payload_leaves:
        raise ValueError("trigger payload must contain at least one leaf")
    for path, value in payload_leaves.items():
        device_value = _value_at_path(device_tree, path)
        if value != device_value:
            raise ValueError(
                f"trigger payload value does not match device tree: {path}"
            )
    return payload_leaves


def _value_at_path(data: dict[str, JsonValue], dotted_path: str) -> JsonValue:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(
                f"trigger payload path does not exist in device tree: {dotted_path}"
            )
        current = current[part]
    return deepcopy(current)


_ENUM_TEXT = {
    "off": "关闭",
    "on": "开启",
    "ready": "就绪",
    "acc": "附件供电",
    "idle": "空闲",
    "charging": "充电中",
    "stop": "已停止",
    "done": "已完成",
    "fault": "故障",
    "unknown": "未知",
    "none": "无",
    "low": "低",
    "medium": "中",
    "high": "高",
    "auto": "自动",
    "manual": "手动",
    "eco": "节能",
    "comfort": "舒适",
    "sport": "运动",
    "playing": "播放中",
    "pause": "已暂停",
    "buffering": "缓冲中",
    "online": "在线",
    "offline": "离线",
    "weak": "信号弱",
    "left": "左",
    "right": "右",
    "inner": "内循环",
    "outer": "外循环",
}


def render_leaf(path: str, value: JsonValue) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if path in ARRAY_PATHS:
        if path == "seat.slot_order":
            return None
        if path == "cabin.dialogue.passengers":
            return f"对话乘员：{'、'.join(value)}"
        objects = value
        return "座舱识别对象：" + "、".join(
            f"{item['object_class']}@{item['seat']}({item['confidence']})"
            for item in objects
        )
    rule = FIELD_RULES[path]
    if rule.kind == "bool":
        value_text = "是" if value else "否"
    elif rule.kind == "enum":
        value_text = _ENUM_TEXT.get(str(value), str(value))
    else:
        value_text = f"{value}{rule.unit}"
    return f"{rule.label}：{value_text}"


def render_payload_value(path: str, value: JsonValue) -> str:
    if value is None:
        return "未知"
    if value == "":
        return "空值"
    rendered = render_leaf(path, value)
    if rendered is None:
        return "无"
    _, separator, value_text = rendered.partition("：")
    return value_text if separator else rendered
