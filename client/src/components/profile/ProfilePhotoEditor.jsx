import { useEffect, useId, useRef, useState } from "react";
import { FiCamera, FiTrash2, FiUpload } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { ConsoleButton as Button } from "../../ui/Button";
import { notify } from "../../ui/Toast";
import UserAvatar from "../../ui/UserAvatar";
import { cx } from "../../ui/tokens";
import { announceProfileUpdated, photoFileError } from "../../utils/avatar";

// The signed-in user's OWN profile photo: upload, change, remove (server/app/routers/profile.py).
// Used from the profile header's avatar and from Edit Profile (Settings → General).
//
// Choosing a file shows it in the same circle it will occupy, and nothing is sent until Save:
// a wrong pick costs nothing. The server crops to a centred square and resizes; the preview
// uses object-cover, which shows that same centre. After a change, the session's user is
// re-read (AuthContext) and the change is announced (utils/avatar), so the profile, the
// sidebar and the account menu update without a reload.
export default function ProfilePhotoEditor({ user, onDone, className = "" }) {
  const auth = useAuth();
  const inputRef = useRef(null);
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const [pending, setPending] = useState(null);       // { file, preview } — chosen, not yet saved
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);              // "save" | "remove"

  // A preview is an object URL; release it when it is replaced or the editor goes away.
  useEffect(() => () => {
    if (pending?.preview) URL.revokeObjectURL(pending.preview);
  }, [pending]);

  const hasPhoto = Boolean(user?.avatar_url);
  const name = user?.full_name || "";

  const choose = () => inputRef.current?.click();

  const onFile = (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";                            // choosing the same file again still fires
    if (!file) return;
    const problem = photoFileError(file);
    if (problem) {
      setPending(null);
      setError(problem);
      return;
    }
    setError(null);
    setPending({ file, preview: URL.createObjectURL(file) });
  };

  const finish = async (updated, message) => {
    await auth?.refresh?.();
    announceProfileUpdated(updated);
    notify.success(message);
    onDone?.(updated);
  };

  const save = async () => {
    if (!pending || busy) return;
    setBusy("save");
    setError(null);
    try {
      // The image itself is the request body; the server judges it by its bytes.
      const { data } = await api.post("/profile/avatar", pending.file, {
        headers: { "Content-Type": pending.file.type },
      });
      setPending(null);
      await finish(data, "Profile photo updated");
    } catch (err) {
      setError(errMsg(err, "Couldn't upload that photo. Try again."));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (busy) return;
    setBusy("remove");
    setError(null);
    try {
      const { data } = await api.delete("/profile/avatar");
      await finish(data, "Profile photo removed");
    } catch (err) {
      setError(errMsg(err, "Couldn't remove your photo. Try again."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={cx("flex flex-col items-center gap-5 sm:flex-row sm:items-center", className)}>
      {pending ? (
        <img
          src={pending.preview}
          alt="Preview of your new profile photo"
          className="h-24 w-24 shrink-0 rounded-full object-cover ring-4 ring-violet-200 dark:ring-violet-500/30"
        />
      ) : (
        <UserAvatar
          name={name}
          src={user?.avatar_url}
          className="h-24 w-24 bg-[linear-gradient(140deg,#bfe3f5_0%,#cdd2f6_45%,#d8c8f2_100%)] text-[32px] font-bold text-violet-900"
        />
      )}

      <div className="min-w-0 space-y-3 text-center sm:text-left">
        {/* Out of the tab order and visually hidden: the buttons below open it. */}
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          tabIndex={-1}
          aria-label="Choose a profile photo"
          aria-describedby={error ? `${errorId} ${hintId}` : hintId}
          onChange={onFile}
        />
        <div className="flex flex-wrap items-center justify-center gap-2 sm:justify-start">
          {pending ? (
            <>
              <Button size="sm" onClick={save} loading={busy === "save"} disabled={busy === "save"}>
                Save photo
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setPending(null)} disabled={busy === "save"}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="secondary" leftIcon={hasPhoto ? FiCamera : FiUpload} onClick={choose} disabled={Boolean(busy)}>
                {hasPhoto ? "Change photo" : "Upload photo"}
              </Button>
              {hasPhoto && (
                <Button
                  size="sm"
                  variant="ghost"
                  leftIcon={FiTrash2}
                  onClick={remove}
                  loading={busy === "remove"}
                  disabled={Boolean(busy)}
                >
                  Remove photo
                </Button>
              )}
            </>
          )}
        </div>
        <p id={hintId} className="text-xs text-slate-500 dark:text-slate-400">
          PNG, JPG or WebP, up to 5 MB. Shown in a circle, cropped to the centre.
        </p>
        {error && (
          <p id={errorId} role="alert" className="text-sm font-medium text-rose-600 dark:text-rose-400">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
