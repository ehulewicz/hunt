import { Cable } from "lucide-react";

import { EmptyPage } from "@/components/empty-page";

export default function IntegrationsPage() {
  return <EmptyPage description="Google login is separate from integrations. Calendar and Tasks connections come after Jobs." icon={Cable} title="Integrations" />;
}
