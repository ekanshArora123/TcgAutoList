# TCGplayer API Reference

All TCGplayer integration code lives in `card-server/src/helpers/tcgplayer/`. Format conversions are in `formatters.ts`.

## Active Listings API

- **Endpoint:** `POST https://mp-search-api.tcgplayer.com/v1/product/{id}/listings?mpfev=2163`
- **Auth:** None (browser-mimicking headers)
- **Payload:** JSON with filters for condition, finish ("printing"), language, seller status. Sorted by `price+shipping` ascending.
- **Pagination:** `from` (offset) and `size` (max 50 per request)
- **Seller filtering:** Only include sellers with rating > 80 and sales > 30 (or "X+" format)
- **Format conversion (internal -> API):**
  - Conditions: `NM` -> `"Near Mint"`, `LP` -> `"Lightly Played"`, `MP` -> `"Moderately Played"`, `HP` -> `"Heavily Played"`, `DMG` -> `"Damaged"`
  - Finishes: `Holo` -> `"Holofoil"`, `Reverse-Holo` -> `"Reverse Holofoil"`, `Regular` -> `"Normal"`
  - 1st Edition: `Holo` + `First Edition` -> `"1st Edition Holofoil"`
  - WOTC sets (Base Set Shadowless, Jungle, Fossil, Gym, Neo, Team Rocket): `Holo` -> `"Unlimited Holofoil"`, `Regular` -> `"Unlimited"`

## Sold Listings API

- **Endpoint:** `POST https://mpapi.tcgplayer.com/v2/product/{id}/latestsales?mpfev=4952`
- **Auth:** `TCGAuthTicket_Production` cookie required for full access (25/page with pagination). Without auth: max 5 results, no pagination.
- **Payload:** `{ variants: number[], listingType: "All", conditions: number[], languages: number[], limit: 25, offset: 0 }`
- **Pagination:** With auth cookie, `offset` and `limit` work. `previousPage`/`nextPage` fields indicate more pages.
- **Fallback (no auth):** Query each condition separately for up to 25 results (5 conditions x 5 each)
- **ID mappings:**
  - Conditions: 1=Near Mint, 2=Lightly Played, 3=Moderately Played, 4=Heavily Played, 5=Damaged
  - Variants (finish): 10=Normal, 11=Holofoil, 77=Reverse Holofoil (product-specific)
  - Languages: 1=English
- **Response fields per sale:** `condition`, `variant`, `language`, `quantity`, `title`, `listingType`, `purchasePrice`, `shippingPrice`, `orderDate`
- **Auth cookie source:** `TCGPLAYER_AUTH_COOKIE` env var = browser `TCGAuthTicket_Production` cookie after login

## Card Info API

- **Endpoint:** `POST https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false&mpfev=2163`
- **Auth:** None (browser-mimicking headers)
- **Payload:** Search query with `productId` filter: `{ algorithm: "sales_synonym_v2", from: 0, size: 1, filters: { term: { productLineName: ["pokemon"], productId: [numericId] } }, listingSearch: { ... } }`
- **Returns:**
  - `productName`, `setName`, `rarityName`, `marketPrice`, `lowestPrice`, `totalListings`, `foilOnly`
  - `customAttributes`: `number`, `hp`, `stage`, `energyType`, `attacks` (1-4), `weakness`, `resistance`, `retreatCost`, `releaseDate`, `flavorText`, `description`
  - `aggregations`: listing counts per condition and per printing (finish)

## Price Points API (not currently used)

- **Endpoint:** `GET https://mpapi.tcgplayer.com/v2/product/{id}/pricepoints`
- **Returns:** `[{ printingType, marketPrice, buylistMarketPrice, listedMedianPrice }]` per finish
- **Note:** Market price is unreliable — not used in pricing algorithm.

## Set Catalog API

- **Endpoint:** `GET https://mpapi.tcgplayer.com/v2/Catalog/SetName/{setId}?mpfev=4952`
- **Auth:** None
- **Returns:** `{ setNameId, name, cleanSetName, urlName, abbreviation, releaseDate, isSupplemental, active, setDescription }`
