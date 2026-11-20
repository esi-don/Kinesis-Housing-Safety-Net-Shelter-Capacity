"""Run with python -m pod2 {download,etl,train,report,all}."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["download","etl","train","report","all"])
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--as-of",default=datetime.now(ZoneInfo("Africa/Kampala")).date().isoformat())
    parser.add_argument("--manifest",help="Relative path of an existing source manifest")
    parser.add_argument("--offline",action="store_true",help="all: reuse the verified latest or selected snapshot")
    parser.add_argument("--threshold",type=float,default=.95,help="Provisional pressure threshold; default .95")
    args = parser.parse_args()
    if not 0 < args.threshold <= 2:
        parser.error("--threshold must be >0 and <=2")
    datetime.strptime(args.as_of,"%Y-%m-%d")
    args.root.mkdir(parents=True,exist_ok=True)
    from . import sources
    if args.command == "download" or (args.command == "all" and not args.offline):
        sources.download(args.root)
    if args.command in ["etl","all"]:
        from . import etl
        etl.run(args.root,args.as_of,args.manifest)
    if args.command in ["train","all"]:
        from . import models
        result = models.run(args.root,args.threshold)
        quality = json.loads((args.root/"artifacts/data_quality.json").read_text())
        packages = {p:importlib.metadata.version(p) for p in ["pandas","numpy","scipy","scikit-learn","joblib","threadpoolctl","tzdata","pytz","python-dateutil","six"]}
        run = {"created_at":datetime.now(timezone.utc).isoformat(), "as_of":args.as_of,
            "python":platform.python_version(),"packages":packages,"results":result,
            "snapshot":quality["manifest"],"threshold":args.threshold,
            "code_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}}
        (args.root/"artifacts/run_manifest.json").write_text(json.dumps(run,indent=2),encoding="utf-8")
        print(json.dumps(result),flush=True)
    if args.command in ["report","all"]:
        from . import report
        print(report.run(args.root),flush=True)

if __name__ == "__main__":
    main()
