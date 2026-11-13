const {spawnSync}=require('child_process');const fs=require('fs');
const base=spawnSync(process.execPath,['/Volumes/workSSD/software development/GovBudgetChecker/scripts/validate_real_browser.cjs'],{env:process.env,stdio:'inherit',timeout:180000});
if(base.status!==0)process.exit(base.status||1);
const all=spawnSync(process.execPath,['/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021/harness/browser_all.cjs'],{env:process.env,stdio:'inherit',timeout:540000});
fs.writeFileSync(`${process.env.UAT_OUTPUT}/all_material_pages_exit.json`,JSON.stringify({exit_code:all.status,required_for_overall_acceptance:true,backup_recovery_continues_even_if_page_suite_fails:true}));
// Preserve this separate suite result; do not stop independent backup/recovery controls.
