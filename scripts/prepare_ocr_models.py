"""Explicitly provision the official offline bundle; never called at runtime.

Downloads are development preparation, not OCR acceptance or human truth.
"""

from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

MODELS = {
    "layout_detection": "PP-DocLayout_plus-L",
    "text_detection": "PP-OCRv5_server_det",
    "text_recognition": "PP-OCRv5_server_rec",
    "table_classification": "PP-LCNet_x1_0_table_cls",
    "wired_table_structure_recognition": "SLANeXt_wired",
    "wireless_table_structure_recognition": "SLANet_plus",
    "wired_table_cells_detection": "RT-DETR-L_wired_table_cell_det",
    "wireless_table_cells_detection": "RT-DETR-L_wireless_table_cell_det",
}


def prepare(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)

    def fetch(pair):
        role, name = pair
        url = f"https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/{name}_infer.tar"
        target = destination / name
        if not (target / "inference.pdiparams").is_file():
            with tempfile.TemporaryDirectory(prefix="gbc-model-") as temp:
                archive = Path(temp) / "model.tar"
                with (
                    urllib.request.urlopen(url, timeout=120) as response,
                    archive.open("wb") as output,
                ):
                    shutil.copyfileobj(response, output)
                with tarfile.open(archive) as bundle:
                    for member in bundle.getmembers():
                        if (
                            Path(member.name).is_absolute()
                            or ".." in Path(member.name).parts
                            or member.issym()
                            or member.islnk()
                        ):
                            raise ValueError("unsafe model archive")
                    bundle.extractall(Path(temp) / "unpacked", filter="data")
                configurations = list((Path(temp) / "unpacked").rglob("inference.yml"))
                if len(configurations) != 1:
                    raise ValueError(f"ambiguous model directory: {name}")
                shutil.copytree(configurations[0].parent, target, dirs_exist_ok=True)
        print(f"Provisioned {name}", flush=True)
        return role, dict(name=name, path=name, source=url)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        bindings = dict(executor.map(fetch, MODELS.items()))
    (destination / "MODELS.json").write_text(
        json.dumps({"schema_version": "gbc.ocr.models.v1", "models": bindings}, indent=2) + "\n"
    )
    manifest = destination / "MANIFEST.sha256"
    rows = [
        hashlib.sha256(path.read_bytes()).hexdigest()
        + "  "
        + path.relative_to(destination).as_posix()
        for path in sorted(destination.rglob("*"))
        if path.is_file() and path != manifest
    ]
    manifest.write_text("\n".join(rows) + "\n")
    print("Manifest:", hashlib.sha256(manifest.read_bytes()).hexdigest(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    prepare(parser.parse_args().destination.resolve())
