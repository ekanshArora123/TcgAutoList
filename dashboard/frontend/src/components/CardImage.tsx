import { useEffect, useState } from "react";

// Shows the first candidate image that loads, falling through the list, then to a
// named placeholder. Used by both raw tiles (just the card image) and graded
// tiles (slab front, then the raw card image as a fallback). Minimal redundancy —
// one component, a per-caller candidate list.
//
// The raw `/api/images/<id>` endpoint fetches missing images on demand, so a
// linked card_id fills in with no reload; graded slab images are downloaded at
// add time and served from `/api/graded-images/<cert>`.
//
// `onShown` reports whether the component is currently rendering an image (true)
// or has fallen through to the placeholder (false) — used by the graded tile to
// hide its header when a picture is available.
export default function CardImage({
  srcs,
  alt,
  onShown,
}: {
  srcs: (string | null | undefined)[];
  alt: string;
  onShown?: (shown: boolean) => void;
}) {
  const candidates = srcs.filter((s): s is string => !!s);
  const [idx, setIdx] = useState(0);
  const src = candidates[idx];
  const shown = !!src;

  useEffect(() => {
    onShown?.(shown);
  }, [shown, onShown]);

  return src ? (
    <img src={src} alt={alt} loading="lazy" onError={() => setIdx((i) => i + 1)} />
  ) : (
    <div className="card-tile-no-image">
      <span>{alt}</span>
    </div>
  );
}
