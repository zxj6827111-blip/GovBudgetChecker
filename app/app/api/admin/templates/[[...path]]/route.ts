import {NextRequest,NextResponse} from "next/server";
import {apiBase} from "@/lib/apiBase";
import {requireBackendAuthHeaders} from "@/lib/routeAuth";
async function proxy(request:NextRequest,context:{params:Promise<{path?:string[]}>}) {
 const auth=await requireBackendAuthHeaders();if(!auth.ok)return auth.response;
 if(Number(request.headers.get('content-length')||0)>22*1024*1024)return NextResponse.json({detail:'上传超过大小限制'},{status:413});
 try {
 const parts=(await context.params).path||[];
 const headers=new Headers(auth.headers);const ct=request.headers.get('content-type');if(ct)headers.set('Content-Type',ct);
 let body:ArrayBuffer|undefined;
 if(request.method==='POST'){
  const reader=request.body?.getReader();const chunks:Uint8Array[]=[];let bytes=0;
  if(reader){while(true){const next=await reader.read();if(next.done)break;bytes+=next.value.byteLength;if(bytes>22*1024*1024){await reader.cancel();return NextResponse.json({detail:'上传超过大小限制'},{status:413});}chunks.push(next.value);}}
  const combined=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){combined.set(chunk,offset);offset+=chunk.length;}body=combined.buffer;
 }
 const response=await fetch(`${apiBase}/api/admin/templates${parts.length?'/'+parts.map(encodeURIComponent).join('/'):''}${request.nextUrl.search}`,{method:request.method,headers,cache:'no-store',signal:AbortSignal.timeout(75000),...(request.method==='POST'?{body}: {})});
 return new NextResponse(response.body,{status:response.status,headers:{'Content-Type':response.headers.get('content-type')||'application/json','Cache-Control':'no-store',...(response.headers.get('content-disposition')?{'Content-Disposition':response.headers.get('content-disposition')!}:{})}});
 }catch{return NextResponse.json({detail:'模板服务暂时不可用'},{status:503});}
}
export const GET=proxy;export const POST=proxy;
