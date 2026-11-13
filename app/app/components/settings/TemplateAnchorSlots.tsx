"use client";

type Anchor = Record<string, any>;
export function TemplateAnchorSlots({ anchor, onChange }: { anchor: Anchor; onChange: (value: Anchor) => void }) {
  const text = Array.from(String(anchor.text || "").normalize("NFKC")).filter(c => !/\s/.test(c)).join("");
  const points = Array.from(text);
  const candidates: Array<{ start: number; end: number; name: boolean; fixed: boolean }> = [];
  for (const match of Array.from(text.matchAll(/(?:20\d{2}|20XX)(?=年)/g))) {
    const prefix = text.slice(0, match.index);
    const start = Array.from(prefix).length;
    candidates.push({ start, end: start + match[0].length, name: false, fixed: prefix.lastIndexOf("《") > prefix.lastIndexOf("》") });
  }
  const name = String(anchor.organization_slot || "").normalize("NFKC").replace(/\s/g, "");
  if (name) {
    let from = 0;
    for (let offset = text.indexOf(name, from); offset >= 0; offset = text.indexOf(name, from)) {
      const start = Array.from(text.slice(0, offset)).length;
      candidates.push({ start, end: start + Array.from(name).length, name: true, fixed: false });
      from = offset + name.length;
    }
  }
  const slots: Anchor[] = anchor.slots || [];
  return <fieldset className="space-y-2 rounded-md border border-border p-3"><legend>逐位置审核槽位</legend>
    <p>只替换所选位置。固定文献年份、金额和科目编码保持原文；位置按归一原文字符计数。</p>
    {!candidates.length && <p>当前锚点没有可选的年度或完整名称位置。</p>}
    {candidates.map(c => <label className="block" key={`${c.start}:${c.end}:${c.name}`}>
      字符 {c.start}–{c.end}：{points.slice(c.start, c.end).join("")}　
      <select aria-label={`槽位${c.start}-${c.end}`} disabled={c.fixed} value={slots.find(s => s.start === c.start && s.end === c.end)?.kind || ""}
        onChange={e => {
          const next = slots.filter(s => s.start !== c.start || s.end !== c.end);
          if (e.target.value) next.push({ start: c.start, end: c.end, kind: e.target.value, source_text: points.slice(c.start, c.end).join("") });
          onChange({ ...anchor, slots: next, annual_slot: next.some(s => s.kind !== "organization_name") });
        }}><option value="">保持固定原文</option>{c.name ? <option value="organization_name">完整机构名称</option> : <><option value="report_year">报告年度</option><option value="comparison_year">同比上一年度</option></>}</select>
      {c.fixed && " 固定文献引用，禁止替换"}
    </label>)}
  </fieldset>;
}
