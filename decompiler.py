import subprocess
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

from config import CFR_URL, CFR_JAR, DECOMPILER_DIR, DECOMPILED_DIR, HYTALE_PACKAGE_PREFIX


def find_java() -> str:
    java_home = shutil.which("java")
    if java_home:
        return java_home
    for candidate in [
        Path("C:/Program Files/Java"),
        Path("C:/Program Files (x86)/Java"),
        Path.home() / ".jdks",
    ]:
        if candidate.exists():
            for jdk in sorted(candidate.iterdir(), reverse=True):
                java = jdk / "bin" / "java.exe"
                if java.exists():
                    return str(java)
    raise FileNotFoundError(
        "Java not found. Install a JDK/JRE or set JAVA_HOME."
    )


def _extract_hytale_classes(jar_path: str, dest_dir: Path) -> int:
    """Extract only com/hypixel/hytale/** .class files from the jar."""
    prefix = HYTALE_PACKAGE_PREFIX.replace(".", "/") + "/"
    count = 0
    with zipfile.ZipFile(jar_path) as z:
        for entry in z.namelist():
            if entry.startswith(prefix) and entry.endswith(".class"):
                target = dest_dir / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(entry))
                count += 1
    return count


def decompile_jar(jar_path: str) -> Path:
    jar = Path(jar_path)
    if not jar.exists():
        raise FileNotFoundError(f"Jar not found: {jar_path}")
    if not jar.suffix == ".jar":
        raise ValueError(f"Not a jar file: {jar_path}")

    if not CFR_JAR.exists():
        DECOMPILER_DIR.mkdir(parents=True, exist_ok=True)
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            resp = client.get(CFR_URL)
            resp.raise_for_status()
            CFR_JAR.write_bytes(resp.content)

    java = find_java()

    output_dir = DECOMPILED_DIR / jar.stem
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        count = _extract_hytale_classes(jar_path, tmp_path)
        if count == 0:
            raise RuntimeError(f"No {HYTALE_PACKAGE_PREFIX} classes found in jar")

        tmp_jar = tmp_path / "hytale_classes.jar"
        with zipfile.ZipFile(str(tmp_jar), "w", zipfile.ZIP_DEFLATED) as zf:
            for cls_file in tmp_path.rglob("*.class"):
                arcname = str(cls_file.relative_to(tmp_path))
                zf.write(cls_file, arcname)

        cmd = [
            java, "-jar", str(CFR_JAR),
            str(tmp_jar),
            "--extraclasspath", str(jar),
            "--outputdir", str(output_dir),
            "--silent", "true",
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if proc.returncode != 0:
        raise RuntimeError(
            f"CFR decompilation failed (exit {proc.returncode}):\n{proc.stderr[:2000]}"
        )

    java_files = list(output_dir.rglob("*.java"))
    if not java_files:
        raise RuntimeError("Decompilation produced no .java files")

    extract_jar_resources(str(jar), output_dir)
    return output_dir


def extract_jar_resources(jar_path: str, output_dir: Path) -> list[Path]:
    resources_dir = output_dir / "_resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    hytale_patterns = ["manifests.json", "migration/"]
    with zipfile.ZipFile(jar_path) as z:
        for entry in z.namelist():
            if entry.endswith("/") or entry.endswith(".class"):
                continue
            if any(entry.startswith(p) or entry == p for p in hytale_patterns):
                target = resources_dir / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(entry))
                extracted.append(target)
    return extracted
