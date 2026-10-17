"""Runtime abstraction: the engine runs identically inside Hermes (plugin ctx)
and standalone (CLI/tests). HermesRuntime adapts the plugin ctx; LocalRuntime
persists under a local data dir."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent.parent


class LocalRuntime:
    """Filesystem-backed runtime for CLI and tests."""

    def __init__(self, data_dir: str | os.PathLike | None = None):
        self.data_dir = Path(data_dir) if data_dir else (PLUGIN_DIR / "data" / "local")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = self.data_dir / "config.json"
        self._llm_fn: Callable[..., Any] | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "events.db"

    @property
    def imports_dir(self) -> Path:
        p = self.data_dir / "imports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_setting(self, key: str, default: Any = None) -> Any:
        cfg = {}
        if self._config_path.exists():
            try:
                cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cfg = {}
        return cfg.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        cfg = {}
        if self._config_path.exists():
            try:
                cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cfg = {}
        cfg[key] = value
        self._config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def get_secret(self, name: str) -> str | None:
        return os.environ.get(name)

    def llm_json(self, system: str, user: str) -> dict | None:
        if self._llm_fn is None:
            return None
        try:
            out = self._llm_fn(system=system, user=user)
            return json.loads(out) if isinstance(out, str) else out
        except Exception:
            return None


class HermesRuntime:
    """Adapts a Hermes plugin ctx. Fails soft on capabilities the host lacks."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._data_dir = None

    @property
    def data_dir(self) -> Path:
        if self._data_dir is None:
            base = None
            for fn in ("plugin_data_dir", "data_dir"):
                f = getattr(self.ctx, fn, None)
                if callable(f):
                    try:
                        base = Path(str(f("local-events")))
                        break
                    except TypeError:
                        try:
                            base = Path(str(f()))
                            break
                        except Exception:
                            continue
            self._data_dir = base or (PLUGIN_DIR / "data" / "local")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    @property
    def db_path(self) -> Path:
        for fn in ("plugin_db",):
            f = getattr(self.ctx, fn, None)
            if callable(f):
                try:
                    p = f("local-events")
                    return Path(str(getattr(p, "path", p)))
                except Exception:
                    break
        return self.data_dir / "events.db"

    @property
    def imports_dir(self) -> Path:
        p = self.data_dir / "imports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_setting(self, key: str, default: Any = None) -> Any:
        try:
            v = self.ctx.get_config(key, default)
        except TypeError:
            v = self.ctx.get_config(key)
        except Exception:
            return default
        return default if v is None else v

    def set_setting(self, key: str, value: Any) -> None:
        try:
            self.ctx.set_config(key, value)
        except Exception:
            pass

    def get_secret(self, name: str) -> str | None:
        scope = getattr(self.ctx, "secret_scope", None)
        getter = getattr(scope, "get_secret", None) if scope else None
        if callable(getter):
            try:
                return getter(name)
            except Exception:
                return None
        return os.environ.get(name)

    def llm_json(self, system: str, user: str) -> dict | None:
        llm = getattr(self.ctx, "llm", None)
        if llm is None:
            return None
        # Hermes plugin LLM access; schema-validated structured calls fail closed.
        for kwargs in (
            {"system": system, "prompt": user, "response_format": "json"},
            {"system": system, "prompt": user},
        ):
            try:
                out = llm.chat(**kwargs)  # type: ignore[union-attr]
                if isinstance(out, dict):
                    return out
                if isinstance(out, str):
                    return json.loads(out)
                if hasattr(out, "parsed") and out.parsed is not None:
                    return out.parsed if isinstance(out.parsed, dict) else json.loads(str(out.parsed))
                if isinstance(out, (list, tuple)) and out and isinstance(out[0], str):
                    return json.loads(out[0])
            except Exception:
                continue
        return None


def home_coord(rt) -> tuple[float | None, float | None]:
    lat = rt.get_setting("home_lat", "")
    lon = rt.get_setting("home_lon", "")
    try:
        return (float(lat), float(lon)) if lat and lon else (None, None)
    except (TypeError, ValueError):
        return (None, None)
