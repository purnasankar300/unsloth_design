from django import forms
from django.db import models

from . import services
from .models import Category, Design, Measurement, Season

SPEC_PREFIX = "spec_"


class NewDesignForm(forms.Form):
    """Create a design and its reference image in one step.

    There is no half-created design without a reference: the reference is what
    the whole tree branches from. Only the two code segments and that image are
    required — a name is a label, and every specification value can be set
    later from the drawer.
    """

    name = forms.CharField(max_length=140, label="Name", required=False,
                           help_text="Optional. Left blank, the design goes by its code.")
    season = forms.ModelChoiceField(queryset=Season.objects.filter(is_active=True), label="Drop")
    category = forms.ModelChoiceField(queryset=Category.objects.filter(is_active=True))
    reference_image = forms.ImageField(label="Reference photo")
    requirement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label="What is this design?",
        help_text="Where the reference came from, and what you intend to change.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["placeholder"] = "Block-print Kurta"

        # The specification grid is data, so the fields are built from whatever
        # the table holds rather than declared here. All optional.
        self.spec_rows = services.spec_choices()
        for field, options, _current in self.spec_rows:
            self.fields[SPEC_PREFIX + field.code] = forms.ChoiceField(
                required=False,
                label=field.label,
                choices=[("", "—")] + [(option.pk, option.label) for option in options],
            )

    def spec_fields(self):
        """The bound spec fields, in table order, for the collapsed section."""
        return [self[SPEC_PREFIX + field.code] for field, _options, _current in self.spec_rows]

    def core_fields(self):
        """Everything that is not a specification value."""
        return [self[name] for name in self.fields if not name.startswith(SPEC_PREFIX)]

    def chosen_specs(self):
        """``(SpecField, option_pk)`` for every value the user actually picked."""
        for field, _options, _current in self.spec_rows:
            value = self.cleaned_data.get(SPEC_PREFIX + field.code)
            if value:
                yield field, int(value)


class NewVersionForm(forms.Form):
    image = forms.ImageField(label="Edited image")
    requirement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label="What did you change?",
        help_text="One line is enough. This is what reviewers read next to the image.",
    )


class CommentForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Comment on this version…"}), label=""
    )


class DesignHeaderForm(forms.ModelForm):
    """Name, drop and category, edited from the drawer.

    Used for validation and widgets only — the write goes through
    ``services.update_design``, which is what knows the code never changes.
    """

    class Meta:
        model = Design
        fields = ["name", "season", "category"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        # A design keeps a drop or a category that has since been retired — the
        # same forgiveness ``spec_choices`` gives a retired option.
        instance = kwargs.get("instance") or self.instance
        for name, model in (("season", Season), ("category", Category)):
            current = getattr(instance, f"{name}_id", None)
            self.fields[name].queryset = model.objects.filter(
                models.Q(is_active=True) | models.Q(pk=current)
            )
            self.fields[name].widget.attrs["class"] = "control"
        self.fields["name"].widget.attrs["class"] = "control"


class AssetsForm(forms.ModelForm):
    """The authoritative real-world assets.

    Without these, an approved design is a picture nobody can manufacture from.
    """

    class Meta:
        model = Design
        fields = ["logo_file", "colour_code", "colour_hex", "notes"]
        widgets = {
            "colour_code": forms.TextInput(attrs={"placeholder": "19-4324 TCX"}),
            "colour_hex": forms.TextInput(attrs={"placeholder": "#4F7C82", "maxlength": 7}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


MeasurementFormSet = forms.inlineformset_factory(
    Design,
    Measurement,
    fields=["name", "value_cm", "order"],
    extra=2,
    can_delete=True,
    widgets={"name": forms.TextInput(attrs={"placeholder": "Chest"})},
)
