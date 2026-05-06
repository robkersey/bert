"""Pydantic v2 models for the profile-spec intermediate representation (IR).

The IR is the canonical, machine-checkable form of a Bluetooth SIG profile
spec. It is produced (semi-)automatically by the parser, then reviewed by a
human, then committed under ``src/bert/profiles/<name>/profile.yaml``. The
runner refuses to load IR that still has ``needs_review`` flags set.

Every node may carry ``confidence`` (0..1, parser's self-reported certainty)
and ``needs_review`` (set by the parser when an extraction was ambiguous).
After human review both are stripped or normalised.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# --------------------------------------------------------------------------- #
# Common mixins / enums                                                       #
# --------------------------------------------------------------------------- #


class Reviewable(BaseModel):
    """Mixin: every IR node may carry parser confidence + review flag."""

    model_config = ConfigDict(extra="forbid")

    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    needs_review: bool = False
    source_anchor: str | None = None  # e.g. "section 4.2 / page 12"


class Requirement(str, Enum):
    """SIG-style requirement levels."""

    MANDATORY = "mandatory"
    OPTIONAL = "optional"
    CONDITIONAL = "conditional"
    EXCLUDED = "excluded"


class CharacteristicProperty(str, Enum):
    BROADCAST = "broadcast"
    READ = "read"
    WRITE_NO_RESP = "write_no_response"
    WRITE = "write"
    NOTIFY = "notify"
    INDICATE = "indicate"
    AUTH_SIGNED_WRITE = "authenticated_signed_writes"
    EXTENDED = "extended_properties"


class SecurityLevel(int, Enum):
    """LE security mode 1 levels."""

    LEVEL_1 = 1  # no security
    LEVEL_2 = 2  # unauthenticated pairing with encryption
    LEVEL_3 = 3  # authenticated pairing with encryption
    LEVEL_4 = 4  # LE Secure Connections, authenticated, encrypted


class PairingMethod(str, Enum):
    JUST_WORKS = "just_works"
    PASSKEY = "passkey"
    OOB = "oob"
    NUMERIC_COMPARISON = "numeric_comparison"


class AdvFlag(str, Enum):
    LE_LIMITED_DISCOVERABLE = "LE_Limited_Discoverable"
    LE_GENERAL_DISCOVERABLE = "LE_General_Discoverable"
    BR_EDR_NOT_SUPPORTED = "BR_EDR_Not_Supported"
    LE_BR_EDR_CONTROLLER = "LE_BR_EDR_Controller"
    LE_BR_EDR_HOST = "LE_BR_EDR_Host"


class Presence(str, Enum):
    REQUIRED = "required"
    REQUIRED_IN_ADV = "required_in_adv"
    REQUIRED_IN_SCAN_RESPONSE = "required_in_scan_response"
    REQUIRED_IN_ADV_OR_SCAN_RESPONSE = "required_in_adv_or_scan_response"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


# --------------------------------------------------------------------------- #
# Value-format descriptions (compact, expandable later)                       #
# --------------------------------------------------------------------------- #


class ValueFormat(Reviewable):
    """Describes how a characteristic value is encoded.

    The runner uses this to parse reads/notifications during behaviour
    assertions. Kept deliberately loose — covering every SIG type up-front
    is out of scope; we add formats as profiles need them.
    """

    format: Literal[
        "uint8",
        "uint16",
        "uint32",
        "int8",
        "int16",
        "string",
        "struct",
        "enum_uint8",
        "raw",
    ]
    fields: list[ValueField] | None = None  # for format="struct"
    range: tuple[int, int] | None = None  # for enum / scalar
    opcodes: dict[str, int] | None = None  # for control-point chars
    units: str | None = None


class ValueField(Reviewable):
    """One field inside a struct-formatted characteristic value."""

    name: str
    type: str  # e.g. "uint8", "uint16", "uint8_or_uint16"
    conditional_on: str | None = None  # e.g. "flags.bit0"


# Forward references resolved at module bottom.


# --------------------------------------------------------------------------- #
# Service / Characteristic / Descriptor                                       #
# --------------------------------------------------------------------------- #


class Descriptor(Reviewable):
    uuid: str
    name: str | None = None
    requirement: Requirement = Requirement.OPTIONAL


class Characteristic(Reviewable):
    uuid: str  # 16-bit short form "0x2A37" or full 128-bit
    name: str | None = None
    requirement: Requirement = Requirement.MANDATORY
    condition: str | None = None  # textual when requirement=conditional
    properties: list[CharacteristicProperty] = Field(default_factory=list)
    cccd: Requirement = Requirement.OPTIONAL  # mandatory if notify/indicate
    permissions: dict[str, bool] = Field(default_factory=dict)
    value: ValueFormat | None = None
    descriptors: list[Descriptor] = Field(default_factory=list)

    @field_validator("uuid")
    @classmethod
    def _normalise_uuid(cls, v: str) -> str:
        v = v.strip()
        if v.lower().startswith("0x"):
            return "0x" + v[2:].upper().zfill(4)
        return v.upper()


class Service(Reviewable):
    uuid: str
    name: str | None = None
    requirement: Requirement = Requirement.MANDATORY
    characteristics: list[Characteristic] = Field(default_factory=list)

    @field_validator("uuid")
    @classmethod
    def _normalise_uuid(cls, v: str) -> str:
        v = v.strip()
        if v.lower().startswith("0x"):
            return "0x" + v[2:].upper().zfill(4)
        return v.upper()


# --------------------------------------------------------------------------- #
# Advertising / GAP                                                            #
# --------------------------------------------------------------------------- #


class IntervalBoundsMs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float
    max: float


class AdvertisingRequirements(Reviewable):
    flags: dict[AdvFlag, Presence] = Field(default_factory=dict)
    service_uuids_in_adv_or_scan_response: list[str] = Field(default_factory=list)
    local_name: dict[str, Presence] | None = None
    interval_ms: IntervalBoundsMs | None = None
    tx_power_dbm: tuple[int, int] | None = None


class GAPRequirements(Reviewable):
    connectable: bool = True
    bondable: Requirement = Requirement.OPTIONAL
    min_security_level: SecurityLevel = SecurityLevel.LEVEL_1
    pairing_methods_allowed: list[PairingMethod] = Field(default_factory=list)
    mtu_min: int = 23
    mtu_preferred: int = 247


# --------------------------------------------------------------------------- #
# Procedures + test cases                                                     #
# --------------------------------------------------------------------------- #


class Procedure(Reviewable):
    """Named behavioural rule referenced by one or more test cases."""

    id: str
    description: str
    bounds: dict[str, IntervalBoundsMs] | None = None
    applies_if: str | None = None  # textual gating expression


class TestCaseSource(str, Enum):
    __test__ = False  # not a pytest collection target

    HOST = "host"  # asserted from Bumble's GATT client observations
    OTA = "ota"  # asserted from the sniffer PCAP
    BOTH = "both"  # cross-referenced


class TestCase(Reviewable):
    __test__ = False  # not a pytest collection target

    id: str  # e.g. "TC_HRP_002"
    title: str
    procedure: str  # name of a registered @testcase function
    source: TestCaseSource = TestCaseSource.HOST
    requires: list[str] = Field(default_factory=list)  # IR path refs, informational
    bound: str | None = None  # references a procedure id
    applies_if: str | None = None
    timeout_s: float = 30.0


# --------------------------------------------------------------------------- #
# Top-level Profile                                                           #
# --------------------------------------------------------------------------- #


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    abbrev: str
    version: str
    source_doc: HttpUrl | None = None
    source_doc_sha256: str | None = None
    role_under_test: Literal["peripheral", "central"] = "peripheral"
    reviewer: str | None = None
    reviewed_at: date | None = None


class Profile(BaseModel):
    """Top-level reviewed profile spec — what the runner consumes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    metadata: Metadata
    services: list[Service] = Field(default_factory=list)
    advertising: AdvertisingRequirements | None = None
    gap: GAPRequirements | None = None
    procedures: list[Procedure] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)

    # ---- Convenience lookups -------------------------------------------- #

    def service(self, uuid: str) -> Service | None:
        target = uuid.upper().replace("0X", "0x")
        return next((s for s in self.services if s.uuid.upper() == target.upper()), None)

    def characteristic(self, service_uuid: str, char_uuid: str) -> Characteristic | None:
        svc = self.service(service_uuid)
        if not svc:
            return None
        return next(
            (c for c in svc.characteristics if c.uuid.upper() == char_uuid.upper()),
            None,
        )

    def procedure(self, pid: str) -> Procedure | None:
        return next((p for p in self.procedures if p.id == pid), None)

    # ---- Review-state helpers ------------------------------------------ #

    def unreviewed_nodes(self) -> list[str]:
        """Return human-readable paths of every node still flagged needs_review."""

        out: list[str] = []

        def walk(obj: object, path: str) -> None:
            if isinstance(obj, Reviewable) and obj.needs_review:
                out.append(path)
            if isinstance(obj, BaseModel):
                for name, _ in obj.__class__.model_fields.items():
                    walk(getattr(obj, name), f"{path}.{name}" if path else name)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{path}[{k!r}]")

        walk(self, "")
        return out


# Resolve forward refs.
ValueFormat.model_rebuild()
