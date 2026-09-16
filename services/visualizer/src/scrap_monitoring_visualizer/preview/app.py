"""FastAPI preview endpoints and bounded request admission."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .store import LatestFrameStore

StatusProvider = Callable[[], Mapping[str, Any]]

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Scrap Monitoring Visualizer</title>
  <style>
    body{margin:0;background:#f4f6f8;color:#202124;font:14px sans-serif}
    main{max-width:1920px;margin:auto;padding:24px}
    h1{font-size:20px;margin:0 0 16px}
    .views{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    figure{min-width:0;margin:0}
    figcaption{margin:0 0 8px}
    .visual{box-sizing:border-box;display:block;width:100%;aspect-ratio:16/9;object-fit:contain;border:1px solid #9aa0a6}
    #model{background:#fff}
    #camera{background:#111}
    .metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-top:16px}
    .metric{background:#fff;border:1px solid #dadce0;padding:10px 12px}
    dt{color:#5f6368;font-size:12px;margin-bottom:4px}
    dd{font-variant-numeric:tabular-nums;margin:0}
    @media(max-width:900px){.views{grid-template-columns:1fr}}
    @media(max-width:900px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
  </style>
</head>
<body><main>
  <h1>Scrap Monitoring Visualizer</h1>
  <section class="views">
    <figure>
      <figcaption><span>3D model</span></figcaption>
      <img class="visual" id="model" alt="Latest rendered frame">
    </figure>
    <figure>
      <figcaption><span>Synthetic camera</span></figcaption>
      <img class="visual" id="camera" alt="Live synthetic camera frame">
    </figure>
  </section>
  <dl class="metrics" aria-label="Shared scene values">
    <div class="metric"><dt>Sequence</dt><dd id="metric-sequence">--</dd></div>
    <div class="metric"><dt>Elapsed</dt><dd id="metric-elapsed">--</dd></div>
    <div class="metric"><dt>Fill</dt><dd id="metric-fill">--</dd></div>
    <div class="metric"><dt>Volume</dt><dd id="metric-volume">--</dd></div>
    <div class="metric"><dt>Phase</dt><dd id="metric-phase">--</dd></div>
    <div class="metric"><dt>Cycle / inlet</dt><dd id="metric-cycle-inlet">--</dd></div>
  </dl>
</main><script>
const model=document.getElementById("model");
const camera=document.getElementById("camera");
const metricSequence=document.getElementById("metric-sequence");
const metricElapsed=document.getElementById("metric-elapsed");
const metricFill=document.getElementById("metric-fill");
const metricVolume=document.getElementById("metric-volume");
const metricPhase=document.getElementById("metric-phase");
const metricCycleInlet=document.getElementById("metric-cycle-inlet");
let displayedRevision=null;
let cameraEnabled=false;
let cameraSocket=null;
let cameraDescriptor=null;
let cameraVisibleUrl=null;
let cameraPendingBlob=null;
let cameraDecoding=false;
let cameraRetryTimer=null;

async function displayLatestCameraFrame(){
  if(cameraDecoding)return;
  cameraDecoding=true;
  while(cameraPendingBlob!==null){
    const blob=cameraPendingBlob;
    cameraPendingBlob=null;
    const nextUrl=URL.createObjectURL(blob);
    await new Promise(resolve=>{
      camera.onload=resolve;
      camera.onerror=resolve;
      camera.src=nextUrl;
    });
    if(cameraVisibleUrl!==null)URL.revokeObjectURL(cameraVisibleUrl);
    cameraVisibleUrl=nextUrl;
  }
  cameraDecoding=false;
}

function reconnectCamera(){
  clearTimeout(cameraRetryTimer);
  if(cameraEnabled)cameraRetryTimer=setTimeout(connectCamera,1000);
}

function connectCamera(){
  if(!cameraEnabled||cameraSocket?.readyState===WebSocket.OPEN||cameraSocket?.readyState===WebSocket.CONNECTING)return;
  cameraDescriptor=null;
  const url=new URL("/camera/v1/stream",window.location.href);
  url.protocol=window.location.protocol==="https:"?"wss:":"ws:";
  const socket=new WebSocket(url);
  cameraSocket=socket;
  socket.binaryType="blob";
  socket.onmessage=event=>{
    if(typeof event.data==="string"){
      try{
        const value=JSON.parse(event.data);
        if(value.type!=="camera_stream_descriptor"||value.version!==1||value.format!=="MJPEG")throw new Error("Unsupported camera stream.");
        cameraDescriptor=value;
      }catch(error){
        socket.close(1002,"invalid descriptor");
      }
      return;
    }
    if(cameraDescriptor===null){
      socket.close(1002,"descriptor required");
      return;
    }
    cameraPendingBlob=event.data;
    displayLatestCameraFrame();
  };
  socket.onerror=()=>socket.close();
  socket.onclose=()=>{
    if(cameraSocket===socket)cameraSocket=null;
    if(cameraEnabled){
      reconnectCamera();
    }
  };
}

function displaySceneValues(scene){
  if(scene===null||scene===undefined){
    for(const node of [metricSequence,metricElapsed,metricFill,metricVolume,metricPhase,metricCycleInlet])node.textContent="--";
    return;
  }
  const inlet=scene.current_inlet_index===null?"-":String(scene.current_inlet_index+1);
  metricSequence.textContent=String(scene.sequence);
  metricElapsed.textContent=scene.elapsed_s.toFixed(1)+" s";
  metricFill.textContent=(scene.surface_fill_ratio*100).toFixed(1)+" %";
  metricVolume.textContent=scene.surface_volume_m3.toFixed(2)+" m3";
  metricPhase.textContent=scene.phase;
  metricCycleInlet.textContent=String(scene.cycle_index)+" / "+inlet;
}

async function refresh(){
  try{
    const response=await fetch("/status",{cache:"no-store"});
    const status=await response.json();
    displaySceneValues(status.scene);
    cameraEnabled=status.synthetic_camera?.camera_enabled===true;
    if(cameraEnabled){
      connectCamera();
    }else{
      if(cameraSocket!==null)cameraSocket.close();
    }
    if(status.frame_revision!==null&&status.frame_revision!==displayedRevision){
      model.src="/frame.png?revision="+status.frame_revision;
      displayedRevision=status.frame_revision;
    }else if(status.frame_revision===null){
      model.removeAttribute("src");
      displayedRevision=null;
    }
  }catch(error){displaySceneValues(null);}
  setTimeout(refresh,500);
}

window.addEventListener("beforeunload",()=>{
  cameraEnabled=false;
  clearTimeout(cameraRetryTimer);
  if(cameraSocket!==null)cameraSocket.close();
  if(cameraVisibleUrl!==null)URL.revokeObjectURL(cameraVisibleUrl);
});
refresh();
</script></body></html>
"""


class RequestGate:
    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("request limit must be positive")
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active <= 0:
                raise RuntimeError("request gate release without acquire")
            self._active -= 1


def create_preview_app(
    frames: LatestFrameStore,
    status_provider: StatusProvider,
    *,
    max_requests: int = 16,
    request_timeout_s: float = 5.0,
) -> FastAPI:
    if request_timeout_s <= 0:
        raise ValueError("request timeout must be positive")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = RequestGate(max_requests)

    @app.middleware("http")
    async def bounded_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not await gate.acquire():
            return JSONResponse({"detail": "request limit exceeded"}, status_code=503)
        try:
            async with asyncio.timeout(request_timeout_s):
                return await call_next(request)
        except TimeoutError:
            return JSONResponse({"detail": "request timeout"}, status_code=504)
        finally:
            await gate.release()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML, headers={"Cache-Control": "no-store"})

    @app.get("/frame.png")
    async def frame() -> Response:
        snapshot = frames.get()
        if snapshot is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        return Response(
            snapshot.png,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Frame-Revision": str(snapshot.revision),
                "X-Sequence": str(snapshot.sequence),
            },
        )

    @app.get("/status")
    async def status() -> JSONResponse:
        payload = dict(status_provider())
        frame_snapshot = frames.get()
        payload["frame_revision"] = (
            frame_snapshot.revision if frame_snapshot is not None else None
        )
        payload["rendered_sequence"] = (
            frame_snapshot.sequence if frame_snapshot is not None else None
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    return app
