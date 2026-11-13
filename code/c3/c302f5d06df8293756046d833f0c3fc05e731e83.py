from pathlib import Path
import sys
p=Path(__file__).with_name("extra_controls.py")
code=p.read_text()
old="CAND = ROOT.parents[1]"
assert code.count(old)==1
code=code.replace(old,"CAND = Path("+repr('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')+")")
exec(compile(code,str(p),"exec"),{"__file__":str(p),"__name__":"__main__"})
