"""Optional semantic embeddings via model2vec (potion-base-8M, ~8 MB, MIT).

This is an optional extra: `pip install model2vec` (plus numpy). When absent,
`available()` is False, `embed()` returns None, and every caller degrades to
today's behavior — no ranking or ingest path requires it. Vectors are stored
in the existing events.embedding BLOB (float32, see store.vec_to_blob)."""

from __future__ import annotations

MODEL_NAME = "minishlab/potion-base-8M"
_DIM = None
_MODEL = None


def available() -> bool:
    try:
        import model2vec  # noqa: F401
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def dimension() -> int | None:
    """Embedding dimension once the model is loadable; None when unavailable."""
    global _DIM
    if not available():
        return None
    vec = embed("dimension probe") or []
    _DIM = len(vec) or None
    return _DIM


def _model():
    global _MODEL
    if _MODEL is None:
        from model2vec import StaticModel

        _MODEL = StaticModel.from_pretrained(MODEL_NAME)
    return _MODEL


def embed(text: str) -> list[float] | None:
    """Embed text; None when the extra is missing, the text is empty, or the
    model can't load (any failure degrades to no-embedding)."""
    if not text or not text.strip():
        return None
    try:
        model = _model()
    except Exception:
        return None
    try:
        return [float(x) for x in model.encode(text)]
    except Exception:
        return None


def event_text(event, venue=None) -> str:
    """The text an event is embedded from: title + category + venue context."""
    parts = [event.title, event.category]
    if event.subcategory:
        parts.append(event.subcategory)
    if venue is not None:
        parts.append(venue.name)
        if venue.venue_type:
            parts.append(venue.venue_type)
    if event.organizer:
        parts.append(event.organizer)
    return " · ".join(p for p in parts if p)


def event_embedding(event, venue=None) -> list[float] | None:
    return embed(event_text(event, venue))
