;; The define scene: the page's defmcp-tool example (docs/12-mcp-tools.md, "From Hy (defmcp-tool)").
;; Prints the McpToolDef the macro made — the name the agent sees, the JSON Schema it reads — and the value of
;; the tool's handler run the way the tool server runs it.
(require doeff-hy.macros [defk defmcp-tool <-])
(import doeff [run])
(import json inspect)

(defk submit-order [symbol side qty]
  {:pre [(: symbol str) (: side str) (: qty int)] :post [(: % str)]}
  f"accepted: {side} {qty} of {symbol}")

(defmcp-tool submit-order-tool
  "Submit a trading order to Kabustation"
  [{:name "symbol" :type "string" :description "Stock symbol code (e.g. 7203)"}
   {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}
   {:name "qty" :type "integer" :description "Quantity to trade"}]
  (<- result (submit-order symbol side qty))
  result)

(print "type:" (. (type submit-order-tool) __name__))
(print "name:" submit-order-tool.name)
(print "description:" submit-order-tool.description)
(print "input_schema:" (json.dumps (.input-schema submit-order-tool) :indent 1))
(print "handler:" submit-order-tool.handler.__name__ (str (inspect.signature submit-order-tool.handler)))
(print "run(handler(\"7203\", \"buy\", 100)):" (repr (run (submit-order-tool.handler "7203" "buy" 100))))
