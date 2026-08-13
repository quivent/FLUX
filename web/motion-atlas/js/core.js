// core.js
const $=id=>document.getElementById(id);
const FUEL_CAPACITY_SEC=6*3600;
const FUEL_LOW_SEC=30*60;
const ATLAS_FIELD=65536;
const resumedJob=sessionStorage.getItem("motionAtlasJob");
// Where the last dispatched atlas stopped, so Continue can pick the path back up
// across a reload. Shape: {start,end,cells}.
const resumedRange=(()=>{try{const r=JSON.parse(sessionStorage.getItem("motionAtlasRange")||"null");return r&&Number.isFinite(r.end)?r:null}catch{return null}})();
const state={studyType:null,runType:"path",activeJob:resumedJob,lastRange:resumedRange,started:0,frames:[],assetSide:"A",acceptedAssetJobs:new Set(),hydratedJobs:new Set(),pendingAssets:new Map(),gpuProcesses:new Map(),preview:new Map(),selectedFlavor:null,model:{known:false,downloaded:false,loaded:false,pendingPreview:!resumedJob},discovery:{started:false,stopped:false,level:0,jobs:new Map(),ready:new Set()}};
const pageStudies=[
