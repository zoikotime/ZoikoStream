import ApiCredentials from "../../components/organization/ApiCredentials";

// /organization/credentials.
//
// The screen itself now lives in components/organization/ApiCredentials, because Settings ->
// Developer mounts the same thing. This route is kept rather than removed: it is linked from
// Quick Actions, the Organization page and Settings, and bookmarks exist — it is simply no
// longer a primary sidebar destination.
export default function Credentials() {
  return <ApiCredentials />;
}
