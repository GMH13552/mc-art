"""A written block model must be a legal Minecraft model -- checked by reading it back.

The bug this exists for: `write_block_model` passed the spec's `elements` straight
through, so a face written `"texture": "wood"` stayed a bare word. In a Minecraft
model a bare word is a PATH, so it resolved to `examplepack:textures/wood.png`,
which does not exist, and every face showed nothing. The model was unusable in
game.

It survived every gate because:

* the spec's own convention IS a bare key, and the engine's audit checked the spec;
* the PREVIEW reads the PNGs off disk, so it looked perfect.

Preview right, artifact broken. So the check runs on the FILE.

    python scripts/check-block-model.py            # both cases
    python scripts/check-block-model.py --fault    # the reverse fixtures
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from mc_art.blockmodel import validate_written_model, write_block_model  # noqa: E402


def _fixture(root: Path) -> Path:
    (root / "textures").mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (16, 16), (140, 110, 70, 255)).save(root / "textures" / "example_planks.png")
    spec = {
        "name": "example_block",
        "namespace": "examplepack",
        "textures": {"wood": "textures/example_planks.png"},
        "elements": [
            {
                "id": "body",
                "from": [0, 0, 0],
                "to": [16, 16, 16],
                "faces": {
                    "up": {"texture": "wood", "uv": [0, 0, 16, 16]},
                    "down": {"texture": "wood", "uv": [0, 0, 16, 16]},
                    "north": {"texture": "wood", "uv": [0, 0, 16, 16]},
                },
            }
        ],
    }
    path = root / "spec.json"
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", action="store_true")
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="mc-art-block-"))
    try:
        spec_path = _fixture(scratch)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))

        print("CASE 1: generate from a spec, then READ THE MODEL BACK")
        result = write_block_model(spec, scratch / "pack", base_dir=scratch)
        model_path = Path(result["model"])
        document = json.loads(model_path.read_text(encoding="utf-8"))
        faces = [
            face["texture"]
            for element in document["elements"]
            for face in (element.get("faces") or {}).values()
        ]
        print("  spec said:      'wood'")
        print("  written model:  %s" % ", ".join(sorted(set(faces))))
        report = validate_written_model(model_path)
        print("  validated:      ok=%s  faces=%d  declared=%s"
              % (report["ok"], report["faces_checked"], report["declared_textures"]))
        case1_green = report["ok"] and all(value.startswith("#") for value in faces)
        print("  -> %s" % ("legal model" if case1_green else "ILLEGAL"))
        print()

        print("REVERSE FIXTURE: put the bare word back, as the bug wrote it")
        document["elements"][0]["faces"]["up"]["texture"] = "wood"
        document["elements"][0]["faces"]["north"]["texture"] = "wood"
        broken_path = model_path.parent / "example_block_broken.json"
        broken_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        broken = validate_written_model(broken_path)
        print("  validated:      ok=%s  problems=%d" % (broken["ok"], len(broken["problems"])))
        for problem in broken["problems"][:2]:
            print("    element %d face %s: %s"
                  % (problem["element"], problem["face"], problem["why"][:160]))
        case2_red = not broken["ok"] and len(broken["problems"]) == 2
        print("  -> %s" % ("REJECTED as required" if case2_red else "NOT REJECTED"))
        print()

        if args.fault:
            print("REVERSE FIXTURE 1: a generated model must be legal")
            print("  %s" % ("PASS (green as required)" if case1_green else "FAIL"))
            print()
            print("REVERSE FIXTURE 2: a bare-word face must be refused")
            print("  %s" % ("PASS (went red as required)" if case2_red else "FAIL (did not go red)"))
            print()
            ok = case1_green and case2_red
            print("fault gates: %s" % ("both behave as required" if ok else "ONE DID NOT"))
            return 0 if ok else 1
        return 0 if (case1_green and case2_red) else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
