/** @type {import('next').NextConfig} */
const API = process.env.ACG_API_BASE || "http://localhost:8000";
module.exports = {
  reactStrictMode: true,
  async rewrites() {
    // proxy /api/* to the FastAPI backend so the client uses same-origin paths
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};
