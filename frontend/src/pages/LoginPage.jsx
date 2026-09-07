import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

// React port of studio.py's render_login / LOGIN_PAGE.
export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      navigate("/studio");
    } catch (err) {
      setError(err.message || "Incorrect username or password.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="centerwrap">
      <div className="wrap">
        <p className="eyebrow">Pilant Studio</p>
        <p className="subeyebrow">one interface · describe it, get a live screen</p>
        <form className="loginform" onSubmit={submit}>
          <input
            type="text"
            placeholder="username"
            autoFocus
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            type="password"
            placeholder="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button type="submit" disabled={submitting}>
            {submitting ? "Logging in…" : "Log in"}
          </button>
        </form>
        {error ? <p className="error">{error}</p> : null}
      </div>
    </div>
  );
}
