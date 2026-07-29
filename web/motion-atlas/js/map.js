// map.js
function checkpoint(name,status="done"){const row=document.querySelector(`[data-step="${name}"]`);if(!row)return;const current=row.classList.contains("active")?"active":row.classList.contains("done")?"done":"";if(current!==status){row.classList.remove("active","done");if(status)row.classList.add(status);renderSpineState()}}
function updateJobReady(){const configured=state.studyType&&$("prompt").value.trim()&&$("id").value.trim()&&numeric("cells")>0&&numeric("batchSize")>0&&numeric("size")>0&&numeric("steps")>0&&numeric("guidance")>0,loaded=state.model.loaded;checkpoint("planned",configured?"done":"");$("planButton").disabled=!loaded||!configured;$("launchButton").disabled=state.model.known&&loaded&&!configured;$("launchButton").querySelector("span").textContent=!state.model.known?"Checking worker":!loaded?(state.model.downloaded?"Load worker":"Download model"):configured?`Start ${state.studyType}`:"Choose loop or atlas";return !!configured&&loaded}
function drawMap(){
  const c=$("mapCanvas"),d=devicePixelRatio||1,r=c.getBoundingClientRect();c.width=r.width*d;c.height=r.height*d;
  const x=c.getContext("2d");x.scale(d,d);x.strokeStyle="#55e7ee22";x.lineWidth=.7;
  for(let i=0;i<18;i++){x.beginPath();x.ellipse(r.width/2,r.height/2,r.width*(.12+i*.027),r.height*(.42-i*.009),0,0,Math.PI*2);x.stroke()}
