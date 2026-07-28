// core.js
const $=id=>document.getElementById(id);
const FUEL_CAPACITY_SEC=6*3600;
const FUEL_LOW_SEC=30*60;
const resumedJob=sessionStorage.getItem("motionAtlasJob");
const state={studyType:null,runType:"path",activeJob:resumedJob,started:0,frames:[],assetSide:"A",acceptedAssetJobs:new Set(),hydratedJobs:new Set(),pendingAssets:new Map(),gpuProcesses:new Map(),preview:new Map(),selectedFlavor:null,model:{known:false,downloaded:false,loaded:false,pendingPreview:!resumedJob},discovery:{started:false,stopped:false,level:0,jobs:new Map(),ready:new Set()}};
const pageStudies=[
