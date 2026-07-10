import { useState } from "react";
import { cardImageUrl } from "../api";

// Shared card image with graceful fallback to a named placeholder. Used by both
// the raw collection tiles and the graded tiles (minimal redundancy).
//
// Whenever a card has a TCGplayer `cardId` we request the image — the backend
// image endpoint fetches it on demand if it's missing, so a card with an id but
// no image on disk (e.g. a graded slab linked to a card_id) fills in with no
// reload. If the card genuinely has no image (or has no id), `onError` falls back
// to the named placeholder. (Slab-specific PSA photos are still a deferred
// feature; a linked card_id reuses the raw card image.)
export default function CardImage({
  cardId,
  alt,
}: {
  cardId: string | null;
  alt: string;
}) {
  const [err, setErr] = useState(false);
  const show = !!cardId && !err;
  return show ? (
    <img src={cardImageUrl(cardId!)} alt={alt} loading="lazy" onError={() => setErr(true)} />
  ) : (
    <div className="card-tile-no-image">
      <span>{alt}</span>
    </div>
  );
}
