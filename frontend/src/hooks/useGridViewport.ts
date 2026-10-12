import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type ViewState = {
  zoom: number
  offsetX: number
  offsetY: number
}

type GridViewportOptions = {
  boardWidth: number
  boardHeight: number
  initialView?: 'fit' | 'actual'
  fitMode?: 'contain' | 'width-priority'
  minZoom?: number
  maxZoom?: number
  zoomStep?: number
}

type PointerAnchor = {
  startX: number
  startY: number
  originOffsetX: number
  originOffsetY: number
  pointerId: number
}

const VIEW_PADDING = 24

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

export function useGridViewport({
  boardWidth,
  boardHeight,
  initialView = 'fit',
  fitMode = 'contain',
  minZoom = 0.4,
  maxZoom = 2.5,
  zoomStep = 0.1,
}: GridViewportOptions) {
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const hasInteractedRef = useRef(false)
  const pointerAnchorRef = useRef<PointerAnchor | null>(null)
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const [view, setView] = useState<ViewState>({
    zoom: 1,
    offsetX: 0,
    offsetY: 0,
  })

  useEffect(() => {
    const element = viewportRef.current
    if (!element) return

    const updateViewportSize = () => {
      const rect = element.getBoundingClientRect()
      setViewportSize({
        width: rect.width,
        height: rect.height,
      })
    }

    updateViewportSize()

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateViewportSize)
      return () => window.removeEventListener('resize', updateViewportSize)
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      setViewportSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      })
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const computeFitView = useCallback((): ViewState => {
    const width = viewportSize.width
    const height = viewportSize.height

    if (!width || !height || !boardWidth || !boardHeight) {
      return {
        zoom: 1,
        offsetX: 0,
        offsetY: 0,
      }
    }

    const safeWidth = Math.max(0, width - VIEW_PADDING * 2)
    const safeHeight = Math.max(0, height - VIEW_PADDING * 2)
    const widthZoom = safeWidth / boardWidth
    const heightZoom = safeHeight / boardHeight
    const fitZoom = clamp(fitMode === 'width-priority' ? Math.min(widthZoom, heightZoom) : Math.min(widthZoom, heightZoom), minZoom, maxZoom)
    const fittedWidth = boardWidth * fitZoom
    const fittedHeight = boardHeight * fitZoom

    return {
      zoom: fitZoom,
      offsetX: fitMode === 'width-priority' ? Math.max(VIEW_PADDING, (width - fittedWidth) / 2) : (width - fittedWidth) / 2,
      offsetY: fitMode === 'width-priority' ? VIEW_PADDING : (height - fittedHeight) / 2,
    }
  }, [boardHeight, boardWidth, fitMode, maxZoom, minZoom, viewportSize.height, viewportSize.width])

  const computeActualView = useCallback((): ViewState => {
    const width = viewportSize.width
    const height = viewportSize.height
    const zoom = clamp(1, minZoom, maxZoom)

    return {
      zoom,
      offsetX: width ? (width - boardWidth * zoom) / 2 : 0,
      offsetY: height ? (height - boardHeight * zoom) / 2 : 0,
    }
  }, [boardHeight, boardWidth, maxZoom, minZoom, viewportSize.height, viewportSize.width])

  const fitToView = useCallback(() => {
    hasInteractedRef.current = false
    setView(computeFitView())
  }, [computeFitView])

  const resetToActual = useCallback(() => {
    hasInteractedRef.current = true
    setView(computeActualView())
  }, [computeActualView])

  const zoomBy = useCallback(
    (direction: 1 | -1, anchorX?: number, anchorY?: number) => {
      hasInteractedRef.current = true

      setView((current) => {
        const nextZoom = clamp(current.zoom + direction * zoomStep, minZoom, maxZoom)
        if (nextZoom === current.zoom) return current

        const fallbackAnchorX = viewportSize.width / 2
        const fallbackAnchorY = viewportSize.height / 2
        const localAnchorX = anchorX ?? fallbackAnchorX
        const localAnchorY = anchorY ?? fallbackAnchorY

        const worldX = (localAnchorX - current.offsetX) / current.zoom
        const worldY = (localAnchorY - current.offsetY) / current.zoom

        return {
          zoom: nextZoom,
          offsetX: localAnchorX - worldX * nextZoom,
          offsetY: localAnchorY - worldY * nextZoom,
        }
      })
    },
    [maxZoom, minZoom, viewportSize.height, viewportSize.width, zoomStep],
  )

  const handleWheel = useCallback(
    (event: WheelEvent) => {
      event.preventDefault()
      const element = viewportRef.current
      if (!element) return

      const rect = element.getBoundingClientRect()
      const anchorX = event.clientX - rect.left
      const anchorY = event.clientY - rect.top
      zoomBy(event.deltaY < 0 ? 1 : -1, anchorX, anchorY)
    },
    [zoomBy],
  )

  useEffect(() => {
    const element = viewportRef.current
    if (!element) return

    const wheelListener = (event: WheelEvent) => handleWheel(event)
    element.addEventListener('wheel', wheelListener, { passive: false })

    return () => {
      element.removeEventListener('wheel', wheelListener)
    }
  }, [handleWheel])

  const beginPan = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return

    event.preventDefault()
    hasInteractedRef.current = true
    pointerAnchorRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originOffsetX: view.offsetX,
      originOffsetY: view.offsetY,
      pointerId: event.pointerId,
    }

    event.currentTarget.setPointerCapture(event.pointerId)
    setIsDragging(true)
  }, [view.offsetX, view.offsetY])

  const handlePointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const anchor = pointerAnchorRef.current
    if (!anchor || anchor.pointerId !== event.pointerId) return

    event.preventDefault()
    const dx = event.clientX - anchor.startX
    const dy = event.clientY - anchor.startY

    setView((current) => ({
      ...current,
      offsetX: anchor.originOffsetX + dx,
      offsetY: anchor.originOffsetY + dy,
    }))
  }, [])

  const handlePointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const anchor = pointerAnchorRef.current
    if (!anchor || anchor.pointerId !== event.pointerId) return

    event.preventDefault()
    pointerAnchorRef.current = null
    setIsDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  useEffect(() => {
    if (!viewportSize.width || !viewportSize.height) return

    if (!hasInteractedRef.current) {
      setView(initialView === 'actual' ? computeActualView() : computeFitView())
    }
  }, [computeActualView, computeFitView, initialView, viewportSize.height, viewportSize.width, boardWidth, boardHeight])

  const transformStyle = useMemo(
    () => ({
      transform: `translate(${view.offsetX}px, ${view.offsetY}px) scale(${view.zoom})`,
      transformOrigin: '0 0',
    }),
    [view.offsetX, view.offsetY, view.zoom],
  )

  return {
    viewportRef,
    view,
    isDragging,
    fitToView,
    resetToActual,
    zoomIn: (anchorX?: number, anchorY?: number) => zoomBy(1, anchorX, anchorY),
    zoomOut: (anchorX?: number, anchorY?: number) => zoomBy(-1, anchorX, anchorY),
    transformStyle,
    beginPan,
    handlePointerMove,
    handlePointerUp,
  }
}
