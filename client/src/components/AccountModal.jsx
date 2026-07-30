import { useState } from "react";
import Modal from "../ui/Modal";
import { ConsoleButton as Button } from "../ui/Button";
import { Input, Label } from "../ui/forms";
import { notify } from "../ui/Toast";
import api, { errMsg } from "../api";
import { useAuth } from "../auth/AuthContext";

const EMPTY_PASSWORD = { current_password: "", new_password: "", confirm: "" };

// Account settings: full name (PATCH /auth/me) and password (POST /auth/change-password).
// Opened from the profile dropdown's "Profile" button in both Topbar and AdminTopbar.
export default function AccountModal({ open, onClose }) {
  const { user, updateUser } = useAuth();
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [savingName, setSavingName] = useState(false);

  const [password, setPassword] = useState(EMPTY_PASSWORD);
  const [savingPassword, setSavingPassword] = useState(false);

  // Render-phase reset (not an effect) each time the modal opens -- see DataTable.jsx
  // for the same pattern.
  const [wasOpen, setWasOpen] = useState(false);
  if (open && !wasOpen) {
    setWasOpen(true);
    setFullName(user?.full_name || "");
    setPassword(EMPTY_PASSWORD);
  } else if (!open && wasOpen) {
    setWasOpen(false);
  }

  const saveName = async (e) => {
    e.preventDefault();
    if (!fullName.trim()) return notify.error("Name is required");
    setSavingName(true);
    try {
      const { data } = await api.patch("/auth/me", { full_name: fullName.trim() });
      updateUser({ full_name: data.full_name });
      notify.success("Profile updated");
    } catch (err) {
      notify.error(errMsg(err));
    } finally {
      setSavingName(false);
    }
  };

  const changePassword = async (e) => {
    e.preventDefault();
    if (!password.current_password) return notify.error("Enter your current password");
    if (password.new_password.length < 8) return notify.error("New password must be at least 8 characters");
    if (password.new_password !== password.confirm) return notify.error("New passwords don't match");
    setSavingPassword(true);
    try {
      await api.post("/auth/change-password", {
        current_password: password.current_password,
        new_password: password.new_password,
      });
      notify.success("Password updated");
      setPassword(EMPTY_PASSWORD);
    } catch (err) {
      notify.error(errMsg(err));
    } finally {
      setSavingPassword(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Account Settings" size="md">
      <div className="space-y-6">
        <form onSubmit={saveName} className="space-y-3">
          <div>
            <Label variant="console">Full name</Label>
            <Input variant="console" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div>
            <Label variant="console">Email</Label>
            <Input variant="console" value={user?.email || ""} disabled className="opacity-60" />
          </div>
          <Button type="submit" size="sm" loading={savingName}>Save name</Button>
        </form>

        <div className="border-t border-slate-100 pt-5 dark:border-slate-800">
          <form onSubmit={changePassword} className="space-y-3">
            <p className="text-sm font-medium text-slate-700 dark:text-slate-200">Change password</p>
            <div>
              <Label variant="console">Current password</Label>
              <Input
                variant="console"
                type="password"
                value={password.current_password}
                onChange={(e) => setPassword((p) => ({ ...p, current_password: e.target.value }))}
                autoComplete="current-password"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label variant="console">New password</Label>
                <Input
                  variant="console"
                  type="password"
                  value={password.new_password}
                  onChange={(e) => setPassword((p) => ({ ...p, new_password: e.target.value }))}
                  autoComplete="new-password"
                />
              </div>
              <div>
                <Label variant="console">Confirm new password</Label>
                <Input
                  variant="console"
                  type="password"
                  value={password.confirm}
                  onChange={(e) => setPassword((p) => ({ ...p, confirm: e.target.value }))}
                  autoComplete="new-password"
                />
              </div>
            </div>
            <Button type="submit" size="sm" loading={savingPassword}>Update password</Button>
          </form>
        </div>
      </div>
    </Modal>
  );
}
