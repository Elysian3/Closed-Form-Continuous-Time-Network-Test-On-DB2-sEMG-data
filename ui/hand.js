// Native WebGL renderer for the bundled skinned glTF hand assets. This only
// implements the compact GLB subset emitted by these models: no runtime library.
const JOINT_COUNT = 25;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const identity = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const multiply = (a, b) => { const o = Array(16).fill(0); for (let c = 0; c < 4; c += 1) for (let r = 0; r < 4; r += 1) for (let k = 0; k < 4; k += 1) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k]; return o; };
const translate = ([x, y, z]) => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1];
const scale = s => [s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, 0, 0, 0, 1];
const rotationX = a => { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]; };
const quaternion = ([x, y, z, w]) => { const xx=x*x,yy=y*y,zz=z*z,xy=x*y,xz=x*z,yz=y*z,wx=w*x,wy=w*y,wz=w*z; return [1-2*(yy+zz),2*(xy+wz),2*(xz-wy),0,2*(xy-wz),1-2*(xx+zz),2*(yz+wx),0,2*(xz+wy),2*(yz-wx),1-2*(xx+yy),0,0,0,0,1]; };
const inverseRotation = m => [m[0],m[4],m[8],0,m[1],m[5],m[9],0,m[2],m[6],m[10],0,0,0,0,1];
const normalize = a => { const n = Math.hypot(...a) || 1; return a.map(v => v / n); };
const sub = (a, b) => a.map((v, i) => v - b[i]);
const dot = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const around = (p, r) => multiply(multiply(translate(p), r), translate(p.map(v => -v)));
const perspective = (fov, aspect, near, far) => { const f=1/Math.tan(fov/2),d=1/(near-far); return [f/aspect,0,0,0,0,f,0,0,0,0,(far+near)*d,-1,0,0,2*far*near*d,0]; };
const lookAt = (eye, at) => { const z=normalize(sub(eye,at)),x=normalize(cross([0,1,0],z)),y=cross(z,x); return [x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]; };

function compile(gl, type, source) { const s=gl.createShader(type); gl.shaderSource(s,source); gl.compileShader(s); if (!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)||'Shader compilation failed.'); return s; }
function createProgram(gl) {
  const vs=`attribute vec3 position; attribute vec3 normal; attribute vec4 joints; attribute vec4 weights;
    uniform mat4 projectionView; uniform mat4 model; uniform mat4 bones[${JOINT_COUNT}]; varying vec3 vNormal; varying vec3 vPosition;
    void main(){ mat4 skin=weights.x*bones[int(joints.x)]+weights.y*bones[int(joints.y)]+weights.z*bones[int(joints.z)]+weights.w*bones[int(joints.w)]; vec4 world=model*skin*vec4(position,1.0); vPosition=world.xyz; vNormal=mat3(model*skin)*normal; gl_Position=projectionView*world; }`;
  const fs=`precision mediump float; varying vec3 vNormal; varying vec3 vPosition; uniform vec3 eye;
    void main(){ vec3 n=normalize(vNormal),l=normalize(vec3(-3.0,5.0,6.0)-vPosition),v=normalize(eye-vPosition); float d=max(dot(n,l),0.0),rim=pow(1.0-max(dot(n,v),0.0),2.2),sp=pow(max(dot(reflect(-l,n),v),0.0),12.0); vec3 skin=vec3(0.82,0.66,0.54); vec3 c=skin*(0.42+d*0.43+max(dot(n,v),0.0)*0.25)+vec3(0.13,0.10,0.07)*rim*0.30+vec3(1.0,0.84,0.63)*sp*0.06; gl_FragColor=vec4(c,0.99); }`;
  const p=gl.createProgram(); gl.attachShader(p,compile(gl,gl.VERTEX_SHADER,vs)); gl.attachShader(p,compile(gl,gl.FRAGMENT_SHADER,fs)); gl.linkProgram(p); if (!gl.getProgramParameter(p,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p)||'Shader linking failed.'); return p;
}
function accessor(document, binary, index) {
  const a=document.accessors[index],v=document.bufferViews[a.bufferView],components={SCALAR:1,VEC2:2,VEC3:3,VEC4:4,MAT4:16}[a.type],Type={5121:Uint8Array,5123:Uint16Array,5126:Float32Array}[a.componentType];
  if (!Type || !components || a.sparse || v.byteStride) throw new Error('The bundled hand uses an unsupported GLB accessor.');
  return new Type(binary.buffer,binary.byteOffset+(v.byteOffset||0)+(a.byteOffset||0),a.count*components);
}
async function loadHand(url) {
  const response=await fetch(url); if (!response.ok) throw new Error(`Could not load hand model (${response.status}).`);
  const data=await response.arrayBuffer(),view=new DataView(data),bytes=new Uint8Array(data); if (view.getUint32(0,true)!==0x46546c67 || view.getUint32(4,true)!==2) throw new Error('Hand model is not a glTF 2.0 binary.');
  const jsonLength=view.getUint32(12,true),jsonType=view.getUint32(16,true),binaryOffset=20+jsonLength; if(jsonType!==0x4e4f534a||view.getUint32(binaryOffset+4,true)!==0x004e4942)throw new Error('Hand GLB chunks are invalid.');
  const doc=JSON.parse(new TextDecoder().decode(bytes.subarray(20,binaryOffset))),binary=bytes.subarray(binaryOffset+8,binaryOffset+8+view.getUint32(binaryOffset,true)),primitive=doc.meshes?.[0]?.primitives?.[0],skin=doc.skins?.[0];
  if(!primitive||!skin||skin.joints.length!==JOINT_COUNT||primitive.attributes.POSITION===undefined||primitive.attributes.NORMAL===undefined||primitive.attributes.JOINTS_0===undefined||primitive.attributes.WEIGHTS_0===undefined)throw new Error('Hand GLB does not have the expected skinned mesh.');
  const jointInfo=skin.joints.map(i=>{const n=doc.nodes[i],orientation=quaternion(n.rotation||[0,0,0,1]);return {name:n.name||'',position:n.translation||[0,0,0],orientation,rest:multiply(translate(n.translation||[0,0,0]),orientation)};});
  return {position:accessor(doc,binary,primitive.attributes.POSITION),normal:accessor(doc,binary,primitive.attributes.NORMAL),joints:accessor(doc,binary,primitive.attributes.JOINTS_0),weights:accessor(doc,binary,primitive.attributes.WEIGHTS_0),indices:accessor(doc,binary,primitive.indices),inverseBind:accessor(doc,binary,skin.inverseBindMatrices),jointInfo};
}
function fingerGroups(jointInfo) {
  const parts=['metacarpal','proximal','intermediate','distal','tip'];
  const groups=['thumb','index-finger','middle-finger','ring-finger','pinky-finger'].map(prefix=>jointInfo.map((joint,index)=>({joint,index})).filter(({joint})=>joint.name.startsWith(prefix)).sort((a,b)=>parts.findIndex(p=>a.joint.name.includes(p))-parts.findIndex(p=>b.joint.name.includes(p))).map(({index})=>index));
  if(groups.some(g=>g.length<4))throw new Error('Hand skeleton lacks expected finger joints.'); return groups;
}

export function createHand(canvas) {
  const gl=canvas.getContext('webgl',{alpha:true,antialias:true}); if(!gl)throw new Error('WebGL is unavailable');
  const program=createProgram(gl),loc={position:gl.getAttribLocation(program,'position'),normal:gl.getAttribLocation(program,'normal'),joints:gl.getAttribLocation(program,'joints'),weights:gl.getAttribLocation(program,'weights'),projectionView:gl.getUniformLocation(program,'projectionView'),model:gl.getUniformLocation(program,'model'),bones:gl.getUniformLocation(program,'bones[0]'),eye:gl.getUniformLocation(program,'eye')};
  const buffers={position:gl.createBuffer(),normal:gl.createBuffer(),joints:gl.createBuffer(),weights:gl.createBuffer(),indices:gl.createBuffer()}; gl.enable(gl.DEPTH_TEST);gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);gl.clearColor(0,0,0,0);
  let target=[.08,.08,.08,.08,.08],current=target.slice(),j10=null,wristTarget=0,wrist=0,handed='right',smoothing=true,models={},active=null,groups=[],disposed=false,contextLost=false,raf=0,yaw=-.45,pitch=.16,dragging=false,last=[0,0];
  // Canonical display basis: GLB +X/+Y/+Z becomes display +Z/-Y/+X.
  let model=identity(); canvas.dataset.modelState='loading';
  function activate(hand){
    active=hand; groups=fingerGroups(hand.jointInfo);
    // The source left mesh is mirrored along its thickness axis. Turn it to
    // present the same anatomical side, keeping positive winding on both hands.
    const side=handed==='left'?-1:1;
    model=multiply(translate([-.005,.025,0]),multiply(scale(10.4),[0,0,side,0,0,-1,0,0,side,0,0,0,0,0,0,1]));
    for(const [name,data] of Object.entries({position:hand.position,normal:hand.normal,joints:hand.joints,weights:hand.weights})){
      gl.bindBuffer(gl.ARRAY_BUFFER,buffers[name]);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,buffers.indices);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,hand.indices,gl.STATIC_DRAW);
  }
  function attr(buffer,location,size,type){gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,size,type,false,0,0);}
  function poseBones(){
    const wristIndex=active.jointInfo.findIndex(j=>j.name==='wrist');
    const root=!j10 && wristIndex>=0?around(active.jointInfo[wristIndex].position,rotationX(wrist)):identity();
    const deform=Array(JOINT_COUNT).fill(root);
    groups.forEach((group,finger)=>{
      let parent=root;
      group.forEach((jointIndex,part)=>{
        const joint=active.jointInfo[jointIndex];
        const pair=j10 ? [j10[finger*2],j10[finger*2+1]] : [current[finger],current[finger]];
        if(finger===0 && part===0){
          // Illustrative thumb opposition across the palm, followed by two
          // flexion joints. Fingertip markers inherit the distal transform.
          if (!j10) parent=multiply(parent,around(joint.position,rotationX(-pair[0]*1.3)));
        } else if(part>0 && part<group.length-1){
          // XR joints point -Z along the digit and -Y out of the palm.
          // Negative local-X flexion therefore bends toward the palm.
          // The ten public values independently drive MCP/PIP.  Any distal
          // bend is a visual continuation of PIP, never a claimed prediction.
          const input = part === 1 ? pair[0] : pair[1];
          const angle=-input*(finger===0?[.9,1.2]:[1.43,1.50,.85])[part-1];
          const localAxis=multiply(multiply(joint.orientation,rotationX(angle)),inverseRotation(joint.orientation));
          parent=multiply(parent,around(joint.position,localAxis));
        }
        deform[jointIndex]=parent;
      });
    });
    const out=new Float32Array(JOINT_COUNT*16);
    for(let i=0;i<JOINT_COUNT;i++){
      const posed=multiply(deform[i],active.jointInfo[i].rest);
      out.set(multiply(posed,active.inverseBind.subarray(i*16,i*16+16)),i*16);
    }
    return out;
  }
  function draw(){if(disposed||contextLost)return;const ease=smoothing?.12:1;current=current.map((v,i)=>v+(target[i]-v)*ease);wrist+=(wristTarget-wrist)*ease;const width=canvas.width,height=canvas.height,aspect=width/Math.max(1,height);gl.viewport(0,0,width,height);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(active){const distance=Math.max(4.15,2.35/(2*Math.tan(.37)*Math.max(aspect,.45))),eye=[Math.sin(yaw)*distance,.22+Math.sin(pitch)*.86,Math.cos(yaw)*distance];gl.useProgram(program);gl.uniformMatrix4fv(loc.projectionView,false,new Float32Array(multiply(perspective(.74,aspect,.05,20),lookAt(eye,[0,.29,.30]))));gl.uniformMatrix4fv(loc.model,false,new Float32Array(model));gl.uniformMatrix4fv(loc.bones,false,poseBones());gl.uniform3fv(loc.eye,new Float32Array(eye));attr(buffers.position,loc.position,3,gl.FLOAT);attr(buffers.normal,loc.normal,3,gl.FLOAT);attr(buffers.joints,loc.joints,4,gl.UNSIGNED_BYTE);attr(buffers.weights,loc.weights,4,gl.FLOAT);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,buffers.indices);gl.drawElements(gl.TRIANGLES,active.indices.length,gl.UNSIGNED_SHORT,0);}raf=requestAnimationFrame(draw);}
  function resize(){const r=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,Math.round(r.width*dpr)),h=Math.max(1,Math.round(r.height*dpr));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}}
  const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(resize);observer?.observe(canvas);resize();
  const down=e=>{dragging=true;last=[e.clientX,e.clientY];canvas.setPointerCapture?.(e.pointerId);},move=e=>{if(!dragging)return;yaw+=(e.clientX-last[0])*.012;pitch=clamp(pitch+(e.clientY-last[1])*.009,-.48,.62);last=[e.clientX,e.clientY];},up=()=>{dragging=false;},lose=e=>{e.preventDefault();contextLost=true;cancelAnimationFrame(raf);canvas.dataset.modelState='error';canvas.dispatchEvent(new CustomEvent('antikythera-webgl-unavailable',{detail:{reason:'context-lost'}}));};
  canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);canvas.addEventListener('webglcontextlost',lose,false);canvas.style.cursor='grab';
  const ready=Promise.all([loadHand(new URL('./assets/hand/right.glb',import.meta.url)),loadHand(new URL('./assets/hand/left.glb',import.meta.url))]).then(([right,left])=>{if(disposed)return;models={right,left};activate(models[handed]);canvas.dataset.modelState='ready';}).catch(error=>{canvas.dataset.modelState='error';canvas.dispatchEvent(new CustomEvent('antikythera-hand-model-error',{detail:{error}}));throw error;});
  draw();return {ready,setPose(pose={}){j10=null;if(Array.isArray(pose.fingers))target=target.map((v,i)=>clamp(Number(pose.fingers[i])||0,0,1));if(Number.isFinite(pose.wrist))wristTarget=pose.wrist;},setJ10(values){if(!Array.isArray(values)||values.length!==10||values.some(value=>!Number.isFinite(value)))throw new Error('J10 pose requires ten finite display values.');j10=values.map(value=>clamp(value,0,1));},setHandedness(value){handed=value==='left'?'left':'right';if(models[handed] && active!==models[handed])activate(models[handed]);},setMotionEnabled(value){smoothing=!!value;},resize,dispose(){disposed=true;cancelAnimationFrame(raf);observer?.disconnect();canvas.removeEventListener('pointerdown',down);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',up);canvas.removeEventListener('pointercancel',up);canvas.removeEventListener('webglcontextlost',lose);Object.values(buffers).forEach(b=>gl.deleteBuffer(b));gl.deleteProgram(program);}};
}
