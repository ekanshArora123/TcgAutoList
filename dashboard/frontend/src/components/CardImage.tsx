import { useState } from "react";

// Shows the first candidate image that loads, falling through the list, then to a
// named placeholder. Used by both raw tiles (just the card image) and graded
// tiles (slab front, then the raw card image as a fallback). Minimal redundancy —
// one component, a per-caller candidate list.
//
// The raw `/api/images/<id>` endpoint fetches missing images on demand, so a
// linked card_id fills in with no reload; graded slab images are downloaded at
// add time and served from `/api/graded-images/<cert>`.
export default function CardImage({
  srcs,
  alt,
}: {
  srcs: (string | null | undefined)[];
  alt: string;
}) {
  const candidates = srcs.filter((s): s is string => !!s);
  const [idx, setIdx] = useState(0);
  const src = candidates[idx];
  return src ? (
    <img src={src} alt={alt} loading="lazy" onError={() => setIdx((i) => i + 1)} />
  ) : (
    <div className="card-tile-no-image">
      <span>{alt}</span>
    </div>
  );
}
