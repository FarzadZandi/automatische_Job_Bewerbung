from __future__ import annotations
import argparse,re,zipfile
from pathlib import Path
import yaml
TEXT={".py",".md",".txt",".json",".yaml",".yml",".csv",".html",".js",".css",".toml",".bat",".command",".ps1"}
def values(path):
 if not path or not path.exists(): return []
 data=yaml.safe_load(path.read_text(encoding="utf-8")) or {};out=[]
 def walk(v):
  if isinstance(v,dict): [walk(x) for x in v.values()]
  elif isinstance(v,list): [walk(x) for x in v]
  elif isinstance(v,(str,int,float)) and len(str(v).strip())>=4: out.append(str(v).strip())
 walk(data);return out
def text(path):
 if path.suffix.lower() in TEXT:return path.read_text(encoding="utf-8",errors="ignore")
 if path.suffix.lower() in {".docx",".xlsx"}:
  with zipfile.ZipFile(path) as z:return " ".join(z.read(n).decode("utf-8",errors="ignore") for n in z.namelist() if n.endswith(".xml"))
 return ""
def main(folder,paths):
 patterns=[]
 for p in paths:patterns+=values(p)
 hits=[]
 for f in folder.rglob("*"):
  if f.is_file():
   s=text(f).casefold()
   if any(p.casefold() in s for p in patterns):hits.append(f)
 [print(p) for p in hits];print(f"hits={len(hits)}");return bool(hits)
if __name__=="__main__":p=argparse.ArgumentParser();p.add_argument("folder",type=Path);p.add_argument("--profile",type=Path);p.add_argument("--answers",type=Path);p.add_argument("--config",type=Path);a=p.parse_args();raise SystemExit(main(a.folder,[a.profile,a.answers,a.config]))
