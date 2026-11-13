import { needsTableIdentity } from "../../../app/app/components/review-workbench/correctionAdapters";
console.log(JSON.stringify({actual_unknown_code: "UNKNOWN_P1_T0", shows_identity_confirmation: needsTableIdentity({table_code:"UNKNOWN_P1_T0"} as any)}, null, 2));
