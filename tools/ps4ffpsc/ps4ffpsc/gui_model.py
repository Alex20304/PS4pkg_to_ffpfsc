from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .i18n import translate
from .runtime import temporary_workspace

PROGRESS_PREFIX = "PS4FFPSC_PROGRESS "
USABLE_SCAN_EXIT_CODES = frozenset({0, 3})
_SCAN_TOTAL_RE = re.compile(
    r"\bscanning (\d+) (?:PKG file\(s\)|unpacked game source\(s\))"
)
_SCAN_ITEM_RE = re.compile(r"\binspecting (?:PKG|unpacked game source):")
_BUILD_STAGE_RE = re.compile(r"\bstage (\d+)/(\d+):")
GAME_BLOCK_REASON_KEYS = {
    "orphan_package": "block_orphan_package",
    "conflicting_base_packages": "block_conflicting_base_packages",
    "incompatible_package": "block_incompatible_package",
    "incompatible_dlc_package": "block_incompatible_dlc_package",
    "unknown_package_kind": "block_unknown_package_kind",
    "invalid_patch_app_version": "block_invalid_patch_app_version",
}


def normalize_pkg_files(
    paths: Iterable[str | Path],
    language: str = "ru",
) -> tuple[Path, ...]:
    """Return unique absolute PKG paths while preserving the user's order."""
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        if path.suffix.lower() != ".pkg":
            raise ValueError(
                translate(
                    "model_pkg_extension_error",
                    language,
                    path=path,
                )
            )
        if not path.is_file():
            raise FileNotFoundError(path)
        seen.add(key)
        result.append(path)
    if not result:
        raise ValueError(translate("model_no_pkg_files", language))
    return tuple(result)


def validate_source(
    mode: str,
    pkg_files: Iterable[str | Path],
    folder: str | Path | None,
    language: str = "ru",
) -> tuple[str, tuple[Path, ...], Path | None]:
    if mode == "files":
        return mode, normalize_pkg_files(pkg_files, language), None
    if mode == "folder":
        if folder is None or not str(folder).strip():
            raise ValueError(translate("model_pkg_folder_missing", language))
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(path)
        return mode, (), path
    if mode == "dump":
        if folder is None or not str(folder).strip():
            raise ValueError(translate("model_dump_folder_missing", language))
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(path)
        return mode, (), path
    raise ValueError(translate("model_unknown_source_mode", language, mode=mode))


def source_cli_arguments(
    mode: str,
    pkg_files: Iterable[str | Path],
    folder: str | Path | None,
    language: str = "ru",
) -> list[str]:
    normalized_mode, normalized_files, normalized_folder = validate_source(
        mode, pkg_files, folder, language
    )
    if normalized_mode == "folder":
        return ["--pkg-dir", str(normalized_folder)]
    if normalized_mode == "dump":
        return ["--dump-dir", str(normalized_folder)]
    arguments: list[str] = []
    for path in normalized_files:
        arguments.extend(["--pkg-file", str(path)])
    return arguments


def temporary_cli_arguments(temp_dir: str | Path) -> list[str]:
    workspace = temporary_workspace(Path(temp_dir))
    return [
        "--unpacked-dir",
        str(workspace / "unpacked"),
        "--work-dir",
        str(workspace / "work"),
        "--temp-dir",
        str(workspace / "tmp"),
    ]


def scan_inventory_path(payload: Any, temp_dir: str | Path) -> Path:
    if isinstance(payload, dict):
        reported = payload.get("inventory")
        if isinstance(reported, str) and reported.strip():
            return Path(reported)
    return temporary_workspace(Path(temp_dir)) / "unpacked" / "package_inventory.json"


def scan_exit_is_usable(exit_code: int) -> bool:
    """Exit 3 means unsupported inputs were reported, not that scan data is unusable."""
    return exit_code in USABLE_SCAN_EXIT_CODES


def parse_progress_event(line: str) -> dict[str, Any] | None:
    marker = line.find(PROGRESS_PREFIX)
    if marker >= 0:
        raw = line[marker + len(PROGRESS_PREFIX) :].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return {"kind": "worker", **payload}
        return None
    match = _SCAN_TOTAL_RE.search(line)
    if match:
        return {"kind": "scan_total", "total": int(match.group(1))}
    if _SCAN_ITEM_RE.search(line):
        return {"kind": "scan_item"}
    match = _BUILD_STAGE_RE.search(line)
    if match:
        return {
            "kind": "build_stage",
            "stage": int(match.group(1)),
            "total": int(match.group(2)),
        }
    return None


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_byte_size(size: int | float) -> str:
    value = max(0.0, float(size))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024.0 or candidate == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def game_source_size(game: dict[str, Any]) -> int:
    packages = [
        *game.get("base", []),
        *game.get("patches", []),
        *game.get("dlc", []),
        *game.get("unknown", []),
    ]
    return sum(
        max(0, int(package.get("size", 0) or 0))
        for package in packages
        if package.get("supported", True) and not package.get("duplicate_of")
    )


def estimate_remaining_seconds(elapsed: float, percent: float) -> float | None:
    if elapsed < 0 or percent <= 0 or percent > 100:
        return None
    if percent >= 100:
        return 0.0
    return elapsed * (100.0 - percent) / percent


def inventory_summary(inventory: dict[str, Any]) -> dict[str, int]:
    games = inventory.get("games", {})
    return {
        "packages": len(inventory.get("packages", [])),
        "games": len(games),
        "buildable": sum(bool(game.get("buildable")) for game in games.values()),
        "unsupported": len(inventory.get("unsupported", [])),
        "conflicts": sum(bool(game.get("conflicts")) for game in games.values()),
    }


def build_error_text(
    payload: Any,
    title_id: str,
    exit_code: int,
    language: str = "ru",
) -> str:
    if isinstance(payload, dict):
        result = payload.get(title_id)
        if isinstance(result, dict):
            error = result.get("error") or result.get("reason")
            if isinstance(error, str) and error.strip():
                return error.strip()
    return translate("model_build_exit_error", language, code=exit_code)


def package_version_text(package: dict[str, Any]) -> str:
    if package.get("kind") == "dlc":
        return str(package.get("entitlement_label") or package.get("app_version") or "—")
    return str(package.get("app_version") or "—")


def game_block_reason(game: dict[str, Any], language: str = "ru") -> str:
    reasons = [*game.get("conflicts", []), *game.get("warnings", [])]
    translated: list[str] = []
    for reason in reasons:
        if reason in GAME_BLOCK_REASON_KEYS:
            translated.append(translate(GAME_BLOCK_REASON_KEYS[reason], language))
        elif reason.startswith("conflicting_patch_version:"):
            translated.append(
                translate(
                    "block_conflicting_patch_version",
                    language,
                    version=reason.partition(":")[2],
                )
            )
        else:
            translated.append(reason)
    fallback = translate("block_not_ready", language)
    return "; ".join(translated) if translated else fallback
