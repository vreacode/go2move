import { Suspense, useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import type { Group, Points as ThreePoints } from "three";
import { BufferGeometry, Color, Float32BufferAttribute, Matrix4, Quaternion, Vector3 } from "three";
import type { SensorFrame } from "../types";

type ViewMode = "follow" | "top" | "free";
type HistoryMode = "realtime" | "history";

type LidarViewProps = {
  frame: SensorFrame | null;
  showPoints: boolean;
  showImu: boolean;
  pointSize: number;
  viewMode: ViewMode;
  historyMode: HistoryMode;
  historySeconds: number;
  resetKey: number;
  carModelForwardAxis: string;
  carModelUpAxis: string;
};

type HistoryEntry = {
  timestamp: number;
  positions: Float32Array;
};

const EMPTY_POINTS = new Float32Array();
const ROS_TO_THREE_MATRIX = new Matrix4().set(
  0, -1, 0, 0,
  0, 0, 1, 0,
  1, 0, 0, 0,
  0, 0, 0, 1,
);
const ROS_TO_THREE_QUAT = new Quaternion().setFromRotationMatrix(ROS_TO_THREE_MATRIX);
const IDENTITY = new Quaternion();

export function LidarView(props: LidarViewProps) {
  return (
    <div className="h-[420px] overflow-hidden rounded-3xl border border-white/10 bg-black/60">
      <Canvas gl={{ antialias: true }} dpr={[1, 1.5]}>
        <color attach="background" args={["#05070c"]} />
        <fog attach="fog" args={["#05070c", 12, 42]} />
        <ambientLight intensity={0.55} />
        <directionalLight position={[8, 10, 6]} intensity={1.1} color="#dbeafe" />
        <pointLight position={[-8, 5, -6]} intensity={0.6} color="#67e8f9" />
        <Suspense fallback={null}>
          <PerspectiveCamera makeDefault position={[4.5, 3.2, 5.6]} fov={48} />
          <gridHelper args={[24, 24, "#1e3a8a", "#1e293b"]} position={[0, 0, 0]} />
          <axesHelper args={[1.8]} />
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.001, 0]}>
            <planeGeometry args={[24, 24]} />
            <meshStandardMaterial color="#0b1220" metalness={0.1} roughness={0.95} transparent opacity={0.45} />
          </mesh>
          <SceneContent {...props} />
        </Suspense>
      </Canvas>
    </div>
  );
}

function SceneContent({
  frame,
  showPoints,
  showImu,
  pointSize,
  viewMode,
  historyMode,
  historySeconds,
  resetKey,
  carModelForwardAxis,
  carModelUpAxis,
}: LidarViewProps) {
  const pointsRef = useRef<ThreePoints>(null);
  const robotRef = useRef<Group>(null);
  const controlsStateRef = useRef<{ resetTick: number }>({ resetTick: resetKey });
  const historyRef = useRef<HistoryEntry[]>([]);
  const followTarget = useRef(new Vector3(0, 0.38, 0));
  const followQuat = useRef(new Quaternion());
  const pointColorRef = useRef<Float32Array>(EMPTY_POINTS);

  const robotBaseQuat = useMemo(() => {
    const forward = axisVector(carModelForwardAxis);
    const up = axisVector(carModelUpAxis);
    return orientObjectBasis(forward, up);
  }, [carModelForwardAxis, carModelUpAxis]);

  const robotWorldQuat = useMemo(() => {
    const imuQuat = toThreeQuaternion(frame?.imu?.orientation);
    return imuQuat.multiply(robotBaseQuat.clone());
  }, [frame?.imu?.orientation, robotBaseQuat]);

  useEffect(() => {
    const now = frame?.timestamp ?? Date.now() / 1000;
    historyRef.current = historyRef.current.filter((entry) => now - entry.timestamp <= historySeconds);
    if (!frame?.points?.length) return;
    const positions = convertPointsToThree(frame.points, historyMode === "history" ? toThreeQuaternion(frame.imu?.orientation) : null);
    historyRef.current.push({ timestamp: now, positions });
  }, [frame, historyMode, historySeconds]);

  const positions = useMemo(() => {
    if (!showPoints) return EMPTY_POINTS;
    const entries = historyMode === "history" ? historyRef.current : historyRef.current.slice(-1);
    if (!entries.length) return EMPTY_POINTS;
    const totalLength = entries.reduce((sum, entry) => sum + entry.positions.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const entry of entries) {
      merged.set(entry.positions, offset);
      offset += entry.positions.length;
    }
    pointColorRef.current = buildColors(merged);
    return merged;
  }, [frame?.seq, showPoints, historyMode, historySeconds]);

  useEffect(() => {
    if (!pointsRef.current) return;
    const geometry = pointsRef.current.geometry as BufferGeometry;
    geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
    geometry.setAttribute("color", new Float32BufferAttribute(pointColorRef.current, 3));
    geometry.computeBoundingSphere();
  }, [positions]);

  useFrame(({ camera }) => {
    followQuat.current.slerp(showImu ? robotWorldQuat : IDENTITY, 0.16);
    if (robotRef.current) {
      robotRef.current.quaternion.slerp(showImu ? robotWorldQuat : robotBaseQuat, 0.18);
    }

    if (viewMode === "follow") {
      const offset = new Vector3(0, 2.8, 5.8).applyQuaternion(followQuat.current);
      const desiredPosition = followTarget.current.clone().add(offset);
      camera.position.lerp(desiredPosition, 0.12);
      camera.lookAt(followTarget.current);
    } else if (viewMode === "top") {
      camera.position.lerp(new Vector3(0, 10.5, 0.001), 0.12);
      camera.lookAt(0, 0, 0);
    } else if (controlsStateRef.current.resetTick !== resetKey) {
      controlsStateRef.current.resetTick = resetKey;
      camera.position.set(4.5, 3.2, 5.6);
      camera.lookAt(0, 0.4, 0);
    }
  });

  return (
    <>
      <group ref={robotRef}>
        <mesh position={[0, 0.2, 0]}>
          <boxGeometry args={[0.82, 0.28, 1.22]} />
          <meshStandardMaterial color="#e2e8f0" metalness={0.15} roughness={0.45} />
        </mesh>
        <mesh position={[0, 0.43, 0.3]}>
          <boxGeometry args={[0.34, 0.11, 0.36]} />
          <meshStandardMaterial color="#38bdf8" emissive="#0ea5e9" emissiveIntensity={0.16} />
        </mesh>
        <mesh position={[0, 0.12, 0.72]}>
          <coneGeometry args={[0.12, 0.34, 16]} />
          <meshStandardMaterial color="#fb7185" />
        </mesh>
      </group>
      <points ref={pointsRef} frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[new Float32Array(), 3]} />
          <bufferAttribute attach="attributes-color" args={[new Float32Array(), 3]} />
        </bufferGeometry>
        <pointsMaterial size={pointSize} vertexColors sizeAttenuation transparent opacity={historyMode === "history" ? 0.7 : 0.92} />
      </points>
    </>
  );
}

function axisVector(axis: string): Vector3 {
  const normalized = axis.trim().toLowerCase();
  const table: Record<string, Vector3> = {
    positive_x: new Vector3(1, 0, 0),
    negative_x: new Vector3(-1, 0, 0),
    positive_y: new Vector3(0, 1, 0),
    negative_y: new Vector3(0, -1, 0),
    positive_z: new Vector3(0, 0, 1),
    negative_z: new Vector3(0, 0, -1),
  };
  return (table[normalized] ?? table.positive_z).clone();
}

function orientObjectBasis(forwardAxis: Vector3, upAxis: Vector3): Quaternion {
  const zAxis = forwardAxis.clone().normalize();
  const yAxis = upAxis.clone().normalize();
  const xAxis = new Vector3().crossVectors(yAxis, zAxis).normalize();
  const fixedYAxis = new Vector3().crossVectors(zAxis, xAxis).normalize();
  const basis = new Matrix4().makeBasis(xAxis, fixedYAxis, zAxis);
  return new Quaternion().setFromRotationMatrix(basis);
}

function toThreeQuaternion(input?: { x: number; y: number; z: number; w: number }): Quaternion {
  if (!input) return new Quaternion();
  const rosQuat = new Quaternion(input.x, input.y, input.z, input.w);
  return ROS_TO_THREE_QUAT.clone().multiply(rosQuat).multiply(ROS_TO_THREE_QUAT.clone().invert());
}

function convertPointsToThree(points: number[], worldQuat: Quaternion | null): Float32Array {
  const out = new Float32Array(points.length);
  const point = new Vector3();
  for (let i = 0; i < points.length; i += 3) {
    point.set(points[i], points[i + 1], points[i + 2]).applyMatrix4(ROS_TO_THREE_MATRIX);
    if (worldQuat) point.applyQuaternion(worldQuat);
    out[i] = point.x;
    out[i + 1] = point.y;
    out[i + 2] = point.z;
  }
  return out;
}

function buildColors(positions: Float32Array): Float32Array {
  if (!positions.length) return EMPTY_POINTS;
  const data = new Float32Array(positions.length);
  const near = new Color("#22d3ee");
  const far = new Color("#f97316");
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i];
    const y = positions[i + 1];
    const z = positions[i + 2];
    const distance = Math.min(Math.sqrt(x * x + y * y + z * z) / 14, 1);
    const color = near.clone().lerp(far, distance);
    data[i] = color.r;
    data[i + 1] = color.g;
    data[i + 2] = color.b;
  }
  return data;
}
