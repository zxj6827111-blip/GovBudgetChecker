import assert from "node:assert/strict";
import { toPreviewBbox } from "../lib/pageGeometry";

// 上海 2022 文旅局：MediaBox 原点 (-12.02, -12.02)，解析坐标比预览图坐标整体小 12.02pt。
const geometry = { "10": [1, 0, 0, 1, 12.0189, 12.0189] };
assert.deepEqual(toPreviewBbox([409.1, 121.5, 469.1, 133.5], 10, geometry)?.map((v) => Number(v.toFixed(2))),
  [421.12, 133.52, 481.12, 145.52]);
// 其他页、几何未取到、框缺失：原样返回，不猜。
assert.deepEqual(toPreviewBbox([1, 2, 3, 4], 9, geometry), [1, 2, 3, 4]);
assert.deepEqual(toPreviewBbox([1, 2, 3, 4], 10, null), [1, 2, 3, 4]);
assert.equal(toPreviewBbox(undefined, 10, geometry), undefined);
// 旋转 90°：四角变换后取外接框。
assert.deepEqual(toPreviewBbox([10, 20, 30, 60], 1, { "1": [0, 1, -1, 0, 100, 0] }), [40, 10, 80, 30]);
console.log("pageGeometry ok");
