import dns from "node:dns/promises";
import net from "node:net";
import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export const runtime = "nodejs";

const MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024;
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 15_000;

function isPrivateIpv4(ip: string): boolean {
  const parts = ip.split(".").map((x) => Number(x));
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return true;
  const [a, b] = parts;
  if (a === 10) return true;
  if (a === 127) return true;
  if (a === 0) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

function isPrivateIpv6(ip: string): boolean {
  const normalized = ip.toLowerCase();
  return (
    normalized === "::1" ||
    normalized.startsWith("fc") ||
    normalized.startsWith("fd") ||
    normalized.startsWith("fe80:") ||
    normalized === "::"
  );
}

function isPrivateIp(ip: string): boolean {
  const kind = net.isIP(ip);
  if (kind === 4) return isPrivateIpv4(ip);
  if (kind === 6) return isPrivateIpv6(ip);
  return true;
}

async function ensureSafeTarget(rawUrl: string): Promise<URL> {
  let target: URL;
  try {
    target = new URL(rawUrl);
  } catch {
    throw new Error("invalid url");
  }

  if (!["http:", "https:"].includes(target.protocol)) {
    throw new Error("only http/https are allowed");
  }

  const host = target.hostname.toLowerCase();
  if (host === "localhost") {
    throw new Error("localhost is not allowed");
  }

  const directIpType = net.isIP(host);
  if (directIpType > 0 && isPrivateIp(host)) {
    throw new Error("private ip is not allowed");
  }

  if (directIpType === 0) {
    const resolved = await dns.lookup(host, { all: true, verbatim: true });
    if (resolved.length === 0) {
      throw new Error("cannot resolve host");
    }
    for (const entry of resolved) {
      if (isPrivateIp(entry.address)) {
        throw new Error("resolved private ip is not allowed");
      }
    }
  }

  return target;
}

async function readLimitedBody(
  stream: ReadableStream<Uint8Array> | null,
  maxBytes: number
): Promise<Uint8Array> {
  if (!stream) {
    throw new Error("empty response body");
  }

  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    total += value.byteLength;
    if (total > maxBytes) {
      throw new Error("download too large");
    }
    chunks.push(value);
  }

  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

function ensurePdfSignature(bytes: Uint8Array) {
  if (bytes.length < 4) {
    throw new Error("downloaded file is too small");
  }
  const header = new TextDecoder().decode(bytes.slice(0, 4));
  if (header !== "%PDF") {
    throw new Error("downloaded file is not a PDF");
  }
}

async function downloadPdfByUrl(rawUrl: string): Promise<{ data: Uint8Array; fileName: string }> {
  let current = await ensureSafeTarget(rawUrl);

  for (let i = 0; i <= MAX_REDIRECTS; i += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch(current.toString(), {
        method: "GET",
        redirect: "manual",
        cache: "no-store",
        signal: controller.signal,
      });

      if ([301, 302, 303, 307, 308].includes(res.status)) {
        const location = res.headers.get("location");
        if (!location) {
          throw new Error("redirect without location");
        }
        current = await ensureSafeTarget(new URL(location, current).toString());
        continue;
      }

      if (!res.ok) {
        throw new Error(`download failed with status ${res.status}`);
      }

      const body = await readLimitedBody(res.body, MAX_DOWNLOAD_BYTES);
      ensurePdfSignature(body);
      const fileName = current.pathname.split("/").pop() || "link.pdf";
      return { data: body, fileName: fileName.toLowerCase().endsWith(".pdf") ? fileName : `${fileName}.pdf` };
    } finally {
      clearTimeout(timer);
    }
  }

  throw new Error("too many redirects");
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const url = body?.url;
    if (!url || typeof url !== "string") {
      return NextResponse.json({ error: "missing url" }, { status: 400 });
    }

    const { data, fileName } = await downloadPdfByUrl(url);
    const file = new File([data], fileName, { type: "application/pdf" });
    const form = new FormData();
    form.set("file", file);

    const upstream = await fetch(`${apiBase}/upload`, {
      method: "POST",
      headers: backendAuthHeaders(),
      body: form as any,
    });
    const text = await upstream.text();
    let json: any;
    try {
      json = JSON.parse(text);
    } catch {
      json = { raw: text };
    }
    return NextResponse.json(json, { status: upstream.status });
  } catch (e: any) {
    return NextResponse.json(
      { error: e?.message || String(e) },
      { status: 400 }
    );
  }
}

