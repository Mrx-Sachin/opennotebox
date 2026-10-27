export function HighlighterOverlay({
  bbox,
}: {
  bbox: [number, number, number, number]
}) {
  const [x0, top, x1, bottom] = bbox
  const left = x0
  const topPos = top
  const width = x1 - x0
  const height = bottom - top

  return (
    <div
      className="absolute highlight-active pointer-events-none z-10"
      style={{
        left: `${left}px`,
        top: `${topPos}px`,
        width: `${Math.max(width, 1)}px`,
        height: `${Math.max(height, 1)}px`,
      }}
    />
  )
}
