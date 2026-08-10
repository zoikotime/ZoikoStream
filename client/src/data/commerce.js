// The doc's initial launch verticals (ZST-LE-COM-001 Executive doctrine). Kept as a fixed
// list rather than free text so catalog/service-profile/cancellation-policy verticals stay
// consistent with each other — see crud/commercial.py's docstring on the Event.category
// coupling this list is meant to line up with.
export const VERTICALS = [
  { value: "memorials", label: "Memorials" },
  { value: "worship", label: "Worship" },
  { value: "weddings", label: "Weddings and Celebrations" },
  { value: "graduations", label: "Graduations" },
  { value: "civic", label: "Civic Events" },
  { value: "corporate", label: "Corporate Events" },
  { value: "conferences", label: "Conferences" },
];
