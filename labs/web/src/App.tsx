import Admin from "./Admin";

export default function App() {
  if (window.location.pathname.replace(/\/+$/, "") === "/admin") return <Admin />;
  return <h1>Learning Factory Labs</h1>;
}
