"""Review one generated family: contour per frame, then frame coherence.

Usage:
    python3 review_family.py /home/gmh/mc-art/outputs/<run>/family.json

Self-contained (Pillow only). It reads the family's own reports rather than
guessing paths:
  * family.json          -> members, each with out_dir / sprite / selected_round
  * round_XX/generated/references.json -> which source frame each member used

The shape host is the reference whose family_member name matches the member
(suffix match, either direction): the planner names a member after its whole
set, so 'crystal_bow_standby' answers source frame 'bow_standby'.
"""
import json
import sys
from pathlib import Path

from PIL import Image


def match(member, wanted):
    member = str(member or "").strip().lower()
    wanted = str(wanted or "").strip().lower()
    if not member or not wanted:
        return 0
    if member == wanted:
        return len(member) + 1
    if wanted.endswith(member) or member.endswith(wanted):
        return len(member)
    return 0


def member_name(reference):
    for note in reference.get("notes") or []:
        if isinstance(note, str) and note.startswith("family_member="):
            return note.split("=", 1)[1].strip()
    name = str(reference.get("name") or "")
    return name.rsplit(":", 1)[-1] if ":" in name else name


def shape_host(member_dir, member, selected_round):
    try:
        index = int(selected_round)
    except (TypeError, ValueError):
        index = 0
    path = Path(member_dir) / ("round_%02d" % index) / "generated" / "references.json"
    if not path.exists():
        return None
    entries = json.loads(path.read_text(encoding="utf-8"))
    candidates = []
    for entry in entries:
        if not isinstance(entry, dict) or "shape" not in (entry.get("roles") or []):
            continue
        source = entry.get("path")
        if isinstance(source, str) and Path(source).exists():
            candidates.append((member_name(entry), Path(source), str(entry.get("name") or "")))
    if not candidates:
        return None
    best = None
    for name, source, label in candidates:
        score = match(name, member)
        if score and (best is None or score > best[0]):
            best = (score, source, label)
    if best:
        return best[1], best[2]
    return candidates[0][1], candidates[0][2]


def opacity(path):
    with Image.open(path) as loaded:
        image = loaded.convert("RGBA")
    return {(x, y) for y in range(image.height) for x in range(image.width)
            if image.getpixel((x, y))[3] >= 8}


def main(family_path):
    family = json.loads(Path(family_path).read_text(encoding="utf-8"))
    members = [row for row in family.get("members", []) if row.get("sprite") and Path(row["sprite"]).exists()]
    if not members:
        raise SystemExit("no rendered members in %s" % family_path)
    print("family: %s  (%d rendered member(s))" % (family.get("set_name"), len(members)))
    generated, sources = [], []
    for row in members:
        sprite = Path(row["sprite"])
        generated.append(sprite)
        found = shape_host(row.get("out_dir"), row.get("name"), row.get("selected_round"))
        if found is None:
            print("  %-26s no shape host recorded" % row.get("name"))
            continue
        host, label = found
        sources.append(host)
        want, got = opacity(host), opacity(sprite)
        union = want | got
        iou = len(want & got) / float(len(union)) if union else 0.0
        print("  %-26s contour IoU vs %-22s = %.4f  identical=%-5s valid=%s" % (
            row.get("name"), label or host.name, iou, want == got, row.get("validation_passed")))
    if len(generated) > 1:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from continuity import pairs, summarise
        summarise("generated", pairs([str(p) for p in generated], 0.35, 24))
        if len(sources) == len(generated):
            summarise("source", pairs([str(p) for p in sources], 0.35, 24))


if __name__ == "__main__":
    main(sys.argv[1])
