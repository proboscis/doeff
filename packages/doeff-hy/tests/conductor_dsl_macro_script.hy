;;; Test doeff-conductor workflow DSL macros build the shared Python IR.

(require doeff-hy.conductor [defworkflow defphase agent! parallel <- workspace!])
(import doeff_conductor.dsl [artifact ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["status"]
                     "properties" {"status" {"type" "string"}}})

(defworkflow sample-conductor-workflow
  :params {"base_ref" str}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 3}}

  (defphase Implement
    :stakes "normal"
    (<- impls
        (parallel
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt "implement auth"
                  :schema RESULT-SCHEMA
                  :workspace (workspace! :from "main")
                  :files #{"auth.py"})
          (agent! :role "implementer"
                  :class "test-verifiable"
                  :prompt "implement search"
                  :schema RESULT-SCHEMA
                  :workspace (workspace! :from "main")
                  :files #{"search.py"}))))

  (artifact (ref "impls")))

(setv expanded (.expand sample-conductor-workflow))
(assert (= (len (lfor node expanded.nodes :if (= node.kind "agent") node)) 2))
(assert (= (len (lfor node expanded.nodes :if (= node.kind "parallel") node)) 1))
