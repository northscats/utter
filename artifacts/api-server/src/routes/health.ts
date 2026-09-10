import { Router, type IRouter, type RequestHandler } from "express";
import { HealthCheckResponse } from "@workspace/api-zod";

const router: IRouter = Router();

export const healthCheck: RequestHandler = (_req, res) => {
  const data = HealthCheckResponse.parse({ status: "ok" });
  res.set("Cache-Control", "no-store").json(data);
};

// The artifact preview uses /api; its existing documented health URL remains.
router.get(["/", "/healthz"], healthCheck);

export default router;
