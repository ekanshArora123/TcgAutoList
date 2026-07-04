import { useState } from "react";
import { cardImageUrl } from "../api";

// Shared card image with graceful fallback to a named placeholder. Used by both
// the raw collection tiles and the graded tiles (minimal redundancy).
// - raw cards pass `hasImage` (from the backend's on-disk check)
// - graded cards pass just a `cardId` (the optional TCGplayer link); when it's
//   null or the image 404s, the placeholder shows. Slab-specific images are a
//   deferred feature — until then a linked card_id reuses the raw card image.
export default function CardImage({
  cardId,
  alt,
  hasImage,
}: {
  cardId: string | null;
  alt: string;
  hasImage?: boolean;
}) {
  const [err, setErr] = useState(false);
  const show = !!cardId && hasImage !== false && !err;
  return show ? (
    <img src={cardImageUrl(cardId!)} alt={alt} loading="lazy" onError={() => setErr(true)} />
  ) : (
    <div className="card-tile-no-image">
      <span>{alt}</span>
    </div>
  );
}
