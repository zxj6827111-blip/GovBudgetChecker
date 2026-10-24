/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingRoot: __dirname,
  typedRoutes: true,
  // Preserve the request origin through middleware redirects, including IP
  // addresses. Next's URL normalization otherwise rewrites them to localhost.
  skipMiddlewareUrlNormalize: true,
};

module.exports = nextConfig;
