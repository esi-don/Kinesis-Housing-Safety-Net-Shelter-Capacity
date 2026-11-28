"""Official CKAN discovery and immutable checksummed source snapshots."""
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/"
PACKAGES = {
    "shelter": "daily-shelter-overnight-service-occupancy-capacity",
    "waitlist": "centralized-waiting-list-activity-for-social-housing",
    "supply": "active-affordable-and-social-housing-units",
}

def fetch(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Pod2HousingResearch/0.1"})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

def discover(name):
    response = json.loads(fetch(API + "package_show?id=" + PACKAGES[name]))
    if not response.get("success"):
        raise ValueError(response)
    return response["result"]

def download(root, names=PACKAGES):
    root = Path(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = root / "data" / "raw" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"downloaded_at": datetime.now(timezone.utc).isoformat(), "sources": []}
    for name in names:
        package = discover(name)
        (destination / f"{name}_metadata.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
        resources = [r for r in package["resources"] if r.get("format", "").upper() == "CSV"
                     and r.get("datastore_active")]
        if not resources:
            raise ValueError(f"No CSV datastore resource for {name}; inspect publisher metadata")
        for resource in resources:
            url = resource["url"]
            body = fetch(url + ("&" if "?" in url else "?") + "format=csv")
            path = destination / f"{name}_{resource['id']}.csv"
            path.write_bytes(body)
            manifest["sources"].append({"pipeline": name, "package": PACKAGES[name],
                "resource_id": resource["id"], "resource_name": resource["name"], "url": url,
                "path": str(path.relative_to(root)), "sha256": hashlib.sha256(body).hexdigest(),
                "metadata_modified": resource.get("metadata_modified"), "bytes": len(body)})
            print(f"Downloaded {name}: {resource['name']} ({len(body):,} bytes)", flush=True)
    path = destination / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / "data" / "latest_manifest.txt").write_text(str(path.relative_to(root)), encoding="utf-8")
    return path

def load_manifest(root, manifest=None):
    root = Path(root)
    path = root / (manifest or (root / "data/latest_manifest.txt").read_text(encoding="utf-8"))
    result = json.loads(path.read_text(encoding="utf-8"))
    for item in result["sources"]:
        if hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {item['path']}")
    return result

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    print(download(parser.parse_args().root))
