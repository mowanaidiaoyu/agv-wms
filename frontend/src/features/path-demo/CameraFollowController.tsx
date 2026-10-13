import { OrbitControls } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useCallback, useEffect, useRef } from 'react'
import type { ElementRef } from 'react'
import { Vector3 } from 'three'
import type { PlaybackStatus, TrajectorySample } from './types'
import {
  constrainCameraAboveGround,
  createPreservedFollowOffset,
  createVehicleFocusPose,
  DEFAULT_VEHICLE_FOCUS_OFFSET,
  FOLLOW_ENTRY_DISTANCE,
  followVehicleTarget,
  isFiniteVector,
  moveCameraAndTarget,
  PATH_DEMO_ORBIT_SETTINGS,
  shouldFollowVehicle,
  VEHICLE_TARGET_HEIGHT,
  type CameraActionRequest,
  type CameraViewMode,
} from './cameraControls'

type CameraTransition =
  | { kind: 'locate' | 'overview'; cameraPosition: Vector3; target: Vector3 }
  | { kind: 'follow'; cameraOffset: Vector3; allowWhilePaused: boolean }

const MIN_FOLLOW_ENTRY_DISTANCE = 4
const MAX_FOLLOW_ENTRY_DISTANCE = 24
const MIN_FOLLOW_CAMERA_LIFT = 2.5

function createFollowEntryOffset(cameraPosition: Vector3, target: Vector3): Vector3 {
  const offset = cameraPosition.clone().sub(target)
  if (!isFiniteVector(offset) || offset.lengthSq() < 0.0001) return new Vector3(...DEFAULT_VEHICLE_FOCUS_OFFSET)
  const distance = offset.length()
  if (distance < MIN_FOLLOW_ENTRY_DISTANCE || distance > MAX_FOLLOW_ENTRY_DISTANCE) {
    offset.setLength(FOLLOW_ENTRY_DISTANCE)
  }
  if (offset.y < MIN_FOLLOW_CAMERA_LIFT) {
    offset.y = MIN_FOLLOW_CAMERA_LIFT
  }
  return offset
}

export function CameraFollowController({
  initialPosition,
  initialTarget,
  sample,
  playbackStatus,
  viewMode,
  actionRequest,
}: {
  initialPosition: [number, number, number]
  initialTarget: [number, number, number]
  sample: TrajectorySample | null
  playbackStatus: PlaybackStatus
  viewMode: CameraViewMode
  actionRequest: CameraActionRequest | null
}) {
  const camera = useThree((state) => state.camera)
  const controlsRef = useRef<ElementRef<typeof OrbitControls>>(null)
  const transitionRef = useRef<CameraTransition | null>(null)
  const sampleRef = useRef(sample)
  const statusRef = useRef(playbackStatus)
  const modeRef = useRef(viewMode)
  const frameVehicleTargetRef = useRef(new Vector3())
  const frameCameraPositionRef = useRef(new Vector3())

  sampleRef.current = sample
  statusRef.current = playbackStatus
  modeRef.current = viewMode

  useEffect(() => {
    const controls = controlsRef.current
    transitionRef.current = null
    camera.position.set(...initialPosition)
    camera.up.set(0, 1, 0)
    controls?.target.set(...initialTarget)
    controls?.update()
    controls?.saveState()
  }, [camera, initialPosition, initialTarget])

  useEffect(() => {
    const controls = controlsRef.current
    if (!controls || !actionRequest) return
    if (actionRequest.type === 'overview') {
      transitionRef.current = {
        kind: 'overview',
        cameraPosition: new Vector3(...initialPosition),
        target: new Vector3(...initialTarget),
      }
      return
    }
    const currentSample = sampleRef.current
    if (!currentSample) return
    if (actionRequest.type === 'switch-follow-target') {
      transitionRef.current = {
        kind: 'follow',
        cameraOffset: createPreservedFollowOffset(camera.position, controls.target),
        allowWhilePaused: true,
      }
      return
    }
    const pose = createVehicleFocusPose(currentSample.world)
    transitionRef.current = { kind: 'locate', ...pose }
  }, [actionRequest, camera.position, initialPosition, initialTarget])

  useEffect(() => {
    const controls = controlsRef.current
    if (!controls) return
    if (viewMode === 'free') {
      if (transitionRef.current?.kind === 'follow') transitionRef.current = null
      return
    }
    if (!sampleRef.current) return
    transitionRef.current = {
      kind: 'follow',
      cameraOffset: createFollowEntryOffset(camera.position, controls.target),
      allowWhilePaused: statusRef.current === 'paused',
    }
  }, [camera, viewMode])

  useFrame((_, deltaSeconds) => {
    const controls = controlsRef.current
    if (!controls) return
    const currentSample = sampleRef.current
    const transition = transitionRef.current
    let changed = false

    if (transition?.kind === 'locate') {
      if (!currentSample) {
        transitionRef.current = null
      } else {
        changed = true
        if (moveCameraAndTarget(camera.position, controls.target, transition.cameraPosition, transition.target, deltaSeconds)) {
          transitionRef.current = null
        }
      }
    } else if (transition?.kind === 'overview') {
      changed = true
      if (moveCameraAndTarget(camera.position, controls.target, transition.cameraPosition, transition.target, deltaSeconds)) {
        transitionRef.current = null
      }
    } else if (
      currentSample
      && (
        shouldFollowVehicle(modeRef.current, statusRef.current, true)
        || (transition?.kind === 'follow' && transition.allowWhilePaused && modeRef.current === 'follow')
      )
    ) {
      const nextTarget = frameVehicleTargetRef.current.set(...currentSample.world)
      nextTarget.y += VEHICLE_TARGET_HEIGHT
      changed = true
      if (transition?.kind === 'follow') {
        const nextCameraPosition = frameCameraPositionRef.current.copy(nextTarget).add(transition.cameraOffset)
        if (moveCameraAndTarget(camera.position, controls.target, nextCameraPosition, nextTarget, deltaSeconds)) {
          transitionRef.current = null
        }
      } else {
        followVehicleTarget(camera.position, controls.target, nextTarget, deltaSeconds)
      }
    }

    if (changed) {
      constrainCameraAboveGround(camera.position, controls.target)
      controls.update()
    }
  })

  const handleControlsStart = useCallback(() => {
    transitionRef.current = null
  }, [])

  const keepCameraAboveGround = useCallback(() => {
    const controls = controlsRef.current
    if (controls && constrainCameraAboveGround(camera.position, controls.target)) controls.update()
  }, [camera])

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      target={initialTarget}
      enableDamping
      dampingFactor={0.12}
      enableRotate={PATH_DEMO_ORBIT_SETTINGS.enableRotate}
      enableZoom={PATH_DEMO_ORBIT_SETTINGS.enableZoom}
      enablePan={PATH_DEMO_ORBIT_SETTINGS.enablePan}
      mouseButtons={PATH_DEMO_ORBIT_SETTINGS.mouseButtons}
      minDistance={1}
      maxDistance={2000}
      minPolarAngle={0.03}
      maxPolarAngle={Math.PI / 2 - 0.02}
      screenSpacePanning
      onStart={handleControlsStart}
      onChange={keepCameraAboveGround}
    />
  )
}
