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
    sup{font-size:.5em;line-height:0}
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
      <canvas class="visual" id="camera" width="960" height="540" aria-label="Live synthetic camera frame"></canvas>
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
let cameraPendingBlob=null;
let cameraDecoding=false;
let cameraReadyBitmap=null;
let cameraRetryTimer=null;
let cameraGeneration=0;
let cameraSourceFps=null;
let cameraSourceWindowS=null;
const cameraPreviewWidth=960;
const cameraPreviewHeight=540;
const cameraBitmapContext=camera.getContext("bitmaprenderer");
const cameraFallbackContext=cameraBitmapContext===null?camera.getContext("2d"):null;
const cameraMetricWindowMs=5000;
const cameraMetricTimes={received:[],decoded:[],presented:[]};
const cameraMetricTotals={received:0,decoded:0,presented:0,dropped_before_decode:0,dropped_before_present:0,decode_errors:0};

function recordCameraMetric(name){
  const now=performance.now();
  const values=cameraMetricTimes[name];
  values.push(now);
  while(values.length>0&&values[0]<now-cameraMetricWindowMs)values.shift();
  cameraMetricTotals[name]++;
}

function cameraMetricRate(values,now){
  while(values.length>0&&values[0]<now-cameraMetricWindowMs)values.shift();
  if(values.length<2)return 0;
  return (values.length-1)*1000/(values[values.length-1]-values[0]);
}

window.__scrapCameraMetrics={
  snapshot(){
    const now=performance.now();
    return {
      version:1,
      sampled_at_ms:now,
      window_s:cameraMetricWindowMs/1000,
      source:{rendered_fps:cameraSourceFps,window_s:cameraSourceWindowS},
      network:{
        received_frames:cameraMetricTotals.received,
        received_fps:cameraMetricRate(cameraMetricTimes.received,now)
      },
      browser:{
        decoded_frames:cameraMetricTotals.decoded,
        decoded_fps:cameraMetricRate(cameraMetricTimes.decoded,now),
        presented_frames:cameraMetricTotals.presented,
        presented_fps:cameraMetricRate(cameraMetricTimes.presented,now),
        presented_last_second:cameraMetricTimes.presented.filter(value=>value>=now-1000).length,
        dropped_before_decode:cameraMetricTotals.dropped_before_decode,
        dropped_before_present:cameraMetricTotals.dropped_before_present,
        decode_errors:cameraMetricTotals.decode_errors
      },
      queues:{
        pending_decode:cameraPendingBlob===null?0:1,
        decode_inflight:cameraDecoding?1:0,
        pending_present:cameraReadyBitmap===null?0:1
      }
    };
  }
};

async function displayLatestCameraFrame(){
  if(cameraDecoding||cameraReadyBitmap!==null)return;
  const blob=cameraPendingBlob;
  if(blob===null)return;
  cameraPendingBlob=null;
  cameraDecoding=true;
  const generation=cameraGeneration;
  try{
    let bitmap;
    try{
      bitmap=await createImageBitmap(blob,{
        resizeWidth:cameraPreviewWidth,
        resizeHeight:cameraPreviewHeight,
        resizeQuality:"high"
      });
    }catch(error){
      cameraMetricTotals.decode_errors++;
      return;
    }
    if(generation!==cameraGeneration){
      bitmap.close();
      return;
    }
    recordCameraMetric("decoded");
    if(cameraReadyBitmap!==null){
      cameraMetricTotals.dropped_before_present++;
      cameraReadyBitmap.close();
    }
    cameraReadyBitmap=bitmap;
  }finally{
    cameraDecoding=false;
    if(cameraReadyBitmap===null&&cameraPendingBlob!==null)displayLatestCameraFrame();
  }
}

function presentLatestCameraFrame(){
  if(cameraReadyBitmap!==null){
    const bitmap=cameraReadyBitmap;
    cameraReadyBitmap=null;
    if(cameraBitmapContext!==null){
      cameraBitmapContext.transferFromImageBitmap(bitmap);
    }else if(cameraFallbackContext!==null){
      cameraFallbackContext.drawImage(bitmap,0,0,camera.width,camera.height);
      bitmap.close();
    }
    recordCameraMetric("presented");
    if(cameraPendingBlob!==null)displayLatestCameraFrame();
  }
  requestAnimationFrame(presentLatestCameraFrame);
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
  cameraGeneration++;
  socket.binaryType="blob";
  socket.onmessage=event=>{
    if(typeof event.data==="string"){
      try{
        const value=JSON.parse(event.data);
        if(value.type!=="camera_stream_descriptor"||value.version!==1||value.format!=="MJPEG"||value.width!==1920||value.height!==1080||value.fps!==30)throw new Error("Unsupported camera stream.");
        cameraDescriptor=value;
        camera.width=cameraPreviewWidth;
        camera.height=cameraPreviewHeight;
      }catch(error){
        socket.close(1002,"invalid descriptor");
      }
      return;
    }
    if(cameraDescriptor===null){
      socket.close(1002,"descriptor required");
      return;
    }
    recordCameraMetric("received");
    if(cameraPendingBlob!==null)cameraMetricTotals.dropped_before_decode++;
    cameraPendingBlob=event.data;
    displayLatestCameraFrame();
  };
  socket.onerror=()=>socket.close();
  socket.onclose=()=>{
    if(cameraSocket===socket){
      cameraSocket=null;
      cameraGeneration++;
      cameraPendingBlob=null;
    }
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
  metricVolume.innerHTML=scene.surface_volume_m3.toFixed(2)+" m<sup>3</sup>";
  metricPhase.textContent=scene.phase;
  metricCycleInlet.textContent=String(scene.cycle_index)+" / "+inlet;
}

async function refresh(){
  try{
    const response=await fetch("/status",{cache:"no-store"});
    const status=await response.json();
    displaySceneValues(status.scene);
    cameraEnabled=status.synthetic_camera?.camera_enabled===true;
    cameraSourceFps=Number.isFinite(status.synthetic_camera?.camera_source_fps)?status.synthetic_camera.camera_source_fps:null;
    cameraSourceWindowS=Number.isFinite(status.synthetic_camera?.camera_source_fps_window_s)?status.synthetic_camera.camera_source_fps_window_s:null;
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
  cameraGeneration++;
  if(cameraReadyBitmap!==null)cameraReadyBitmap.close();
});
requestAnimationFrame(presentLatestCameraFrame);
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
