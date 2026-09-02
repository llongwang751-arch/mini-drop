package taskkind

import (
	"fmt"
	"math"
	"net/url"
	"slices"
	"unicode/utf8"
)

// ValidateOptions applies the generated TaskKind-specific rules. Unknown keys
// remain available for provenance metadata, matching the JSON Schema extension
// policy used by diagnostic campaigns.
func ValidateOptions(kind Kind, options map[string]any) error {
	for _, rule := range kind.OptionRules {
		value, exists := options[rule.Name]
		if !exists || value == nil {
			continue
		}
		switch rule.Type {
		case "boolean":
			if _, ok := value.(bool); !ok {
				return fmt.Errorf("option %s must be boolean", rule.Name)
			}
		case "integer":
			number, ok := value.(float64)
			if !ok || math.Trunc(number) != number {
				return fmt.Errorf("option %s must be integer", rule.Name)
			}
			integer := int(number)
			if integer < rule.Minimum || (rule.Maximum > 0 && integer > rule.Maximum) {
				return fmt.Errorf("option %s is outside the allowed range", rule.Name)
			}
		case "string":
			text, ok := value.(string)
			if !ok {
				return fmt.Errorf("option %s must be string", rule.Name)
			}
			length := utf8.RuneCountInString(text)
			if length < rule.MinLength || (rule.MaxLength > 0 && length > rule.MaxLength) {
				return fmt.Errorf("option %s has invalid length", rule.Name)
			}
			if len(rule.Enum) > 0 && !slices.Contains(rule.Enum, text) {
				return fmt.Errorf("option %s has an unsupported value", rule.Name)
			}
			if rule.Format == "uri" {
				parsed, err := url.ParseRequestURI(text)
				if err != nil || parsed.Scheme == "" || parsed.Host == "" {
					return fmt.Errorf("option %s must be an absolute URI", rule.Name)
				}
			}
		default:
			return fmt.Errorf("option %s has an unknown generated rule", rule.Name)
		}
	}
	return nil
}
