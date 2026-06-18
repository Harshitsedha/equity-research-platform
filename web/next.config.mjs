/** @type {import('next').NextConfig} */
const nextConfig = {
  // The API origin is a SERVER-ONLY secret (HARD RULE 3): it is read in RSCs via
  // process.env.FASTAPI_BASE_URL and never exposed to the browser. Deliberately
  // NOT placed under `env`/`publicRuntimeConfig` — that would inline it client-side.
  reactStrictMode: true,
};

export default nextConfig;
