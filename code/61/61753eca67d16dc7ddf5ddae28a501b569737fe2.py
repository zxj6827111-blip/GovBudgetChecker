from pathlib import Path
import json,multiprocessing,os,platform
from src.services.pdf_parse_process import _parse_pdf_sync,probe_memory_limit_enforceable,PdfParseLimitExceeded,PdfParseError
file=Path('/tmp/memory-control.pdf');file.write_bytes(b'%PDF controlled allocation hook before parsing')
os.environ['PDF_PARSE_TEST_ALLOCATE_MB']='512'
result={'platform':platform.system(),'production_module':'src.services.pdf_parse_process','kernel_probe_enforced':probe_memory_limit_enforceable(128)}
try:
 _parse_pdf_sync(file,{'memory_mb':128,'timeout_seconds':10,'start_method':'fork'})
 result.update(allocation_blocked=False,error='unexpected success')
except (PdfParseLimitExceeded,PdfParseError) as e:
 result.update(allocation_blocked='memory' in str(e).lower(),exception=type(e).__name__,detail=str(e))
result['remaining_children']=len(multiprocessing.active_children())
result['passed']=result['kernel_probe_enforced'] and result['allocation_blocked'] and not result['remaining_children']
print(json.dumps(result));raise SystemExit(0 if result['passed'] else 2)
