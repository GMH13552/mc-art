"""Reference evidence as text: what a plan author needs, with no model."""

from __future__ import annotations

from .contracts import ReferenceAsset


def _appearance_reference_evidence(references: list[ReferenceAsset]) -> str:
    """Give the painter lossless small-raster evidence without analysis noise.

    Every attached reference remains the primary evidence.  For 64px-or-less
    sprites the text side channel mirrors every source pixel as a compact
    palette legend plus rows, so vision ambiguity cannot turn an original
    texture into a three-colour summary.  Large atlases are supplied as images
    and only identified by their role and dimensions.
    """
    entries: list[str] = []
    for reference in references:
        features = reference.features
        label = "%s roles=%s size=%sx%s" % (
            reference.name,
            ",".join(role.value for role in reference.roles),
            features.get("width", "?"),
            features.get("height", "?"),
        )
        # The router's rationale is semantic evidence.  Omitting it here made
        # a painter see two equally-sized opaque tiles and freely swap a
        # side/bark reference with a top/end-grain reference.  Keep this
        # compact: the attached pixels remain the primary evidence, while the
        # note explains what a structurally similar raster is meant to teach.
        notes = " ".join(str(note).strip() for note in reference.notes if str(note).strip())
        if notes:
            label += " intent=" + notes
        pixel_text = str(features.get("pixel_text", "")).strip()
        if pixel_text and max(int(features.get("width", 65) or 65), int(features.get("height", 65) or 65)) <= 64:
            entries.append(label + "\nSOURCE_PIXELS (legend and rows; . is transparent):\n" + pixel_text)
        else:
            entries.append(label)
    return "\n\n".join(entries) or "- none"



def shape_authority(expanded, preferred_name):
    """The one member of a family whose silhouette answers this request."""
    from .reference_geometry import _reference_member_name, family_member_match

    if not preferred_name or len(expanded) < 2:
        return None
    best = None
    for asset in expanded:
        score = family_member_match(_reference_member_name(asset), preferred_name)
        if score and (best is None or score > best[0]):
            best = (score, asset)
    return best[1] if best else None
