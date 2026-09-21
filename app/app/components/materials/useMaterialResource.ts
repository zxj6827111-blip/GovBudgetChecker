"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * useMaterialResource：材料台账页面的统一取数钩子。
 *
 * 三件必须做对的事
 * ----------------
 * 1. **加载中 / 失败 / 空 / 正常四态分得开**。失败时保留错误信息并显示重试入口，
 *    绝不退化成"空列表"——"查不到"和"查不了"对用户是两件事。
 * 2. **不做本地兜底数据**。其他页面有 local-fallback（演示数据），材料台账不能有：
 *    台账里凭空出现的材料会直接被当成真实台账读，比空页面危险得多。
 * 3. **过期响应丢弃**。筛选条件变化快时，先发的慢响应不能覆盖后发的快响应，
 *    否则页面会显示与当前筛选不符的数据。
 */
export interface MaterialResourceState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/**
 * @param url 请求地址；null 表示"当前不该发请求"（例如部门矩阵还没选年度）。
 * @param parse 响应体 → 契约对象；返回 null 表示响应不符合契约（按错误处理，
 *              不按"空数据"处理 —— 把格式错误显示成"没有材料"是最危险的一种假结论）。
 */
export function useMaterialResource<T>(
  url: string | null,
  parse: (payload: unknown) => T | null,
): MaterialResourceState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(url));
  const [nonce, setNonce] = useState(0);
  const requestId = useRef(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (!url) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }

    const current = requestId.current + 1;
    requestId.current = current;
    let cancelled = false;
    setLoading(true);

    async function load() {
      try {
        const response = await fetch(url as string, { cache: "no-store" });
        const payload = await response.json().catch(() => null);
        if (cancelled || requestId.current !== current) {
          return;
        }
        if (!response.ok) {
          const detail =
            payload && typeof payload === "object" && "detail" in payload
              ? String((payload as { detail?: unknown }).detail ?? "")
              : "";
          setError(detail || `请求失败（HTTP ${response.status}）`);
          setData(null);
        } else {
          const parsed = parse(payload);
          if (parsed === null) {
            setError("返回内容不符合材料台账契约（缺少 data/meta）");
            setData(null);
          } else {
            setError(null);
            setData(parsed);
          }
        }
      } catch (fetchError) {
        if (cancelled || requestId.current !== current) {
          return;
        }
        setError(fetchError instanceof Error ? fetchError.message : "网络请求失败");
        setData(null);
      } finally {
        if (!cancelled && requestId.current === current) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [url, nonce, parse]);

  return { data, error, loading, reload };
}

export default useMaterialResource;
